
# class EarlyStopping():
#     def __init__(self, patience=3, delta=0.0):
#         self.patience = patience
#         self.delta = delta

#     def update(metric):


class ValidationTracker():
    def __init__(self):
        self.best = None

    def update(self, metric):
        if self.best is None:
            self.best = metric
        else:
            if metric < self.best:
                self.best = metric
                return True
            else:
                return False