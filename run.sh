#!/bin/bash

CUDA_VISIBLE_DEVICES=0,1
for model in resnet56 #resnet32 resnet44 resnet56 resnet110 resnet1202
do
    echo "python -m torch.distributed.launch --nproc_per_node=2 trainer.py  --arch=$model  --save-dir=save_$model |& tee -a log_$model"
    python -m torch.distributed.launch --nproc_per_node=2 trainer.py  --arch=$model  --save-dir=save_$model |& tee -a log_$model
done