from core.utils.flow_viz import flow_to_image
import sys
sys.path.append('core')

from PIL import Image
import argparse
import os
import time
import numpy as np
import torch
import torch.nn.functional as F
import matplotlib.pyplot as plt

import datasets
from utils import flow_viz
from utils import frame_utils

from raft import RAFT
from utils.utils import InputPadder, forward_interpolate

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

@torch.no_grad()
def create_sintel_submission(model, iters=32, warm_start=False, output_path='sintel_submission'):
    """ Create submission for the Sintel leaderboard """
    model.eval()
    for dstype in ['clean', 'final']:
        test_dataset = datasets.MpiSintel(split='test', aug_params=None, dstype=dstype)
        
        flow_prev, sequence_prev = None, None
        for test_id in range(len(test_dataset)):
            image1, image2, (sequence, frame) = test_dataset[test_id]
            if sequence != sequence_prev:
                flow_prev = None
            
            padder = InputPadder(image1.shape)
            image1, image2 = padder.pad(image1[None].to(device), image2[None].to(device))

            flow_low, flow_pr = model(image1, image2, iters=iters, flow_init=flow_prev, test_mode=True) #NOTE can pass the model a warm start using flow_init
            flow = padder.unpad(flow_pr[0]).permute(1, 2, 0).cpu().numpy()

            if warm_start:
                flow_prev = forward_interpolate(flow_low[0])[None].to(device)
            
            output_dir = os.path.join(output_path, dstype, sequence)
            output_file = os.path.join(output_dir, 'frame%04d.flo' % (frame+1))

            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            frame_utils.writeFlow(output_file, flow)
            sequence_prev = sequence


@torch.no_grad()
def create_kitti_submission(model, iters=24, output_path='kitti_submission'):
    """ Create submission for the Sintel leaderboard """
    model.eval()
    test_dataset = datasets.KITTI(split='testing', aug_params=None)

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    for test_id in range(len(test_dataset)):
        image1, image2, (frame_id, ) = test_dataset[test_id]
        padder = InputPadder(image1.shape, mode='kitti')
        image1, image2 = padder.pad(image1[None].to(device), image2[None].to(device))

        _, flow_pr = model(image1, image2, iters=iters, test_mode=True)
        flow = padder.unpad(flow_pr[0]).permute(1, 2, 0).cpu().numpy()

        output_filename = os.path.join(output_path, frame_id)
        frame_utils.writeFlowKITTI(output_filename, flow)


@torch.no_grad()
def create_mhof_submission(model, iters=24, output_path='mhof_submission'):
    model.eval()
    test_dataset = datasets.MHOF(split='test', aug_params=None)

    SCALE_INPUT = 2.0

    SUB_SIZE = 160

    for test_id in range(len(test_dataset)):
        image1, image2, test_out = test_dataset[test_id]

        image1 = image1[None].to(device)
        image2 = image2[None].to(device)

        _, _, h_in, w_in = image1.size()
        scaled_in_size = ((int) (SCALE_INPUT*h_in), (int) (SCALE_INPUT*w_in))
        image1 = F.interpolate(image1, scaled_in_size, mode='bilinear', align_corners=False)
        image2 = F.interpolate(image2, scaled_in_size, mode='bilinear', align_corners=False)

        _, flow_pr = model(image1, image2, iters=iters, test_mode=True)

        flow_pr = flow_pr.cpu() / SCALE_INPUT


        # Downsample to the resolution needed by the submission
        flow_sub= F.interpolate(flow_pr, (SUB_SIZE, SUB_SIZE), mode='bilinear', align_corners=False)
        
        flow_sub = flow_sub.squeeze(0).permute(1, 2, 0)
        flow_sub = flow_sub.cpu().numpy()

        # Divide by 20 here to match normalized output from PWC-Net
        flow_sub = flow_sub / 20.0

        # flow_sub_rgb = flow_to_image(flow_sub)
        # plt.imshow(flow_sub_rgb)
        # plt.show()

        output_filename = os.path.join(output_path, test_out)
        output_dir = os.path.dirname(output_filename)
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        frame_utils.writeFlow(output_filename, flow_sub, encoding=np.float16) 


@torch.no_grad()
def validate_chairs(model, iters=24):
    """ Perform evaluation on the FlyingChairs (test) split """
    model.eval()
    epe_list = []

    val_dataset = datasets.FlyingChairs(split='validation')
    for val_id in range(len(val_dataset)):
        image1, image2, flow_gt, _ = val_dataset[val_id]
        image1 = image1[None].to(device) #NOTE the None index is just used to unsqueeze(0), add the batch dimension
        image2 = image2[None].to(device)

        _, flow_pr = model(image1, image2, iters=iters, test_mode=True)
        epe = torch.sum((flow_pr[0].cpu() - flow_gt)**2, dim=0).sqrt()
        epe_list.append(epe.view(-1).numpy())

    epe = np.mean(np.concatenate(epe_list))
    print("Validation Chairs EPE: %f" % epe)
    return {'chairs': epe}


@torch.no_grad()
def validate_sintel(model, iters=32):
    """ Peform validation using the Sintel (train) split """
    model.eval()
    results = {}
    for dstype in ['clean', 'final']:
        val_dataset = datasets.MpiSintel(split='training', dstype=dstype)
        epe_list = []

        for val_id in range(len(val_dataset)):
            image1, image2, flow_gt, _ = val_dataset[val_id]
            image1 = image1[None].to(device)
            image2 = image2[None].to(device)

            padder = InputPadder(image1.shape) #NOTE pad here to make input dimension divisible by 8
            image1, image2 = padder.pad(image1, image2)

            flow_low, flow_pr = model(image1, image2, iters=iters, test_mode=True)
            flow = padder.unpad(flow_pr[0]).cpu() #NOTE remember to unpad if we padded before

            epe = torch.sum((flow - flow_gt)**2, dim=0).sqrt()
            epe_list.append(epe.view(-1).numpy())

        epe_all = np.concatenate(epe_list)
        epe = np.mean(epe_all)
        px1 = np.mean(epe_all<1)
        px3 = np.mean(epe_all<3)
        px5 = np.mean(epe_all<5)

        print("Validation (%s) EPE: %f, 1px: %f, 3px: %f, 5px: %f" % (dstype, epe, px1, px3, px5))
        results[dstype] = np.mean(epe_list)

    return results


@torch.no_grad()
def validate_kitti(model, iters=24):
    """ Peform validation using the KITTI-2015 (train) split """
    model.eval()
    val_dataset = datasets.KITTI(split='training')

    out_list, epe_list = [], []
    for val_id in range(len(val_dataset)):
        image1, image2, flow_gt, valid_gt = val_dataset[val_id]
        image1 = image1[None].to(device)
        image2 = image2[None].to(device)

        padder = InputPadder(image1.shape, mode='kitti') #NOTE pad here to make input dimension divisible by 8
        image1, image2 = padder.pad(image1, image2)

        flow_low, flow_pr = model(image1, image2, iters=iters, test_mode=True)
        flow = padder.unpad(flow_pr[0]).cpu()

        epe = torch.sum((flow - flow_gt)**2, dim=0).sqrt()
        mag = torch.sum(flow_gt**2, dim=0).sqrt()

        epe = epe.view(-1)
        mag = mag.view(-1)
        val = valid_gt.view(-1) >= 0.5

        out = ((epe > 3.0) & ((epe/mag) > 0.05)).float()
        epe_list.append(epe[val].mean().item())
        out_list.append(out[val].cpu().numpy())

    epe_list = np.array(epe_list)
    out_list = np.concatenate(out_list)

    epe = np.mean(epe_list)
    f1 = 100 * np.mean(out_list)

    print("Validation KITTI: %f, %f" % (epe, f1))
    return {'kitti-epe': epe, 'kitti-f1': f1}


#NOTE should not need to pad for HOF since image dimension are already divisible by 8
@torch.no_grad()
def validate_mhof(model, iters=24): #TODO play around with this
    model.eval()
    val_dataset = datasets.MHOF(split='val')

    SCALE_INPUT = 2.0

    epe_list = []
    for val_id in range(len(val_dataset)):
        image1, image2, flow_gt, _ = val_dataset[val_id]

        image1 = image1[None].to(device)
        image2 = image2[None].to(device)

        _, _, h_in, w_in = image1.size()
        scaled_in_size = ((int) (SCALE_INPUT*h_in), (int) (SCALE_INPUT*w_in))
        image1 = F.interpolate(image1, scaled_in_size, mode='bilinear', align_corners=False)
        image2 = F.interpolate(image2, scaled_in_size, mode='bilinear', align_corners=False)

        _, flow_pr = model(image1, image2, iters=iters, test_mode=True)
   
        flow_pr = flow_pr.cpu() / SCALE_INPUT
        flow_pr = F.interpolate(flow_pr, (h_in, w_in), mode='bilinear', align_corners=False)
        flow_pr = flow_pr.squeeze(0)

        epe = torch.sum((flow_pr - flow_gt)**2, dim=0).sqrt()
        epe_list.append(epe.view(-1).numpy())

        # flow_rgb = flow_to_image(flow_pr[0].cpu().permute(1,2,0).numpy())
        # flow_gt_rgb = flow_to_image(flow_gt.permute(1,2,0).numpy())
        
        # error_gray = ((epe / epe.max()).numpy() * 255).astype(np.uint8)

        # plt.subplot(2,3,1)
        # plt.imshow(image1[0].cpu().permute(1,2,0).numpy().astype(np.uint8))
        # plt.subplot(2,3,2)
        # plt.imshow(image2[0].cpu().permute(1,2,0).numpy().astype(np.uint8))
        # plt.subplot(2,3,4)
        # plt.imshow(flow_gt_rgb)
        # plt.subplot(2,3,5)
        # plt.imshow(flow_rgb)
        # plt.subplot(2,3,6)
        # plt.imshow(error_gray)
        # plt.show()

        print("EPE for image: %f" % epe.numpy().mean())
    epe = np.mean(np.concatenate(epe_list))
    print("Validation MHOF EPE: %f" % epe)
    return {"mhof-epe": epe}


# def flow2rgb(flow_map, max_value=None): #NOTE this one takes in a numpy array
#     flow_map_np = flow_map
#     _, h, w = flow_map_np.shape
#     flow_map_np[:,(flow_map_np[0] == 0) & (flow_map_np[1] == 0)] = float('nan')
#     rgb_map = np.ones((3,h,w)).astype(np.float32)
#     if max_value is not None:
#         normalized_flow_map = flow_map_np / max_value
#     else:
#         normalized_flow_map = flow_map_np / (np.abs(flow_map_np).max())
#     rgb_map[0] += normalized_flow_map[0]
#     rgb_map[1] -= 0.5*(normalized_flow_map[0] + normalized_flow_map[1])
#     rgb_map[2] += normalized_flow_map[1]
#     rgb_flow = rgb_map.clip(0,1)
#     rgb_flow = (rgb_flow * 255).astype(np.uint8).transpose(1,2,0)
#     return rgb_flow

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', help="restore checkpoint")
    parser.add_argument('--dataset', help="dataset for evaluation")
    parser.add_argument('--small', action='store_true', help='use small model')
    parser.add_argument('--mixed_precision', action='store_true', help='use mixed precision')
    parser.add_argument('--alternate_corr', action='store_true', help='use efficent correlation implementation')
    args = parser.parse_args()

    model = torch.nn.DataParallel(RAFT(args))
    model.load_state_dict(torch.load(args.model, map_location=torch.device('cpu')))

    model.to(device)
    model.eval()

    # create_sintel_submission(model.module, warm_start=True)
    # create_kitti_submission(model.module)

    # create_mhof_submission(model.module)

    with torch.no_grad():
        if args.dataset == 'chairs':
            validate_chairs(model.module)

        elif args.dataset == 'sintel':
            validate_sintel(model.module)

        elif args.dataset == 'kitti':
            validate_kitti(model.module)

        elif args.dataset == 'mhof':
            validate_mhof(model.module)


