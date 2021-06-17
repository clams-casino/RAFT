#!/bin/bash
mkdir -p checkpoints
python3 -u train.py --name raft-mhof --stage mhof --validation mhof --restore_ckpt models/raft-things.pth --gpus 0 --num_steps 200000 --batch_size 12 --lr 0.000125 --image_size 448 320 --wdecay 0.00001 --gamma=0.85 --mixed_precision