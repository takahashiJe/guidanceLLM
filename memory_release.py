import gc
import torch

# モデル・トークナイザ削除
del model
del tokenizer

# PythonのGC強制
gc.collect()

# CUDAメモリ解放
torch.cuda.empty_cache()
torch.cuda.ipc_collect()
