import torch
print(torch.cuda.is_available())  # True になればOK
print(torch.cuda.get_device_name(0))
