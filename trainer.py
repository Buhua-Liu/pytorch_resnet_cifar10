import argparse
import os
import time
from datetime import datetime

import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn

import torch.optim
import torch.utils.data
import torchvision.transforms as transforms
import torchvision.datasets as datasets
from torch.utils.tensorboard import SummaryWriter

import resnet

model_names = [name for name in resnet.__dict__
    if name.islower() and not name.startswith("__")
                     and name.startswith("resnet")
                     and callable(resnet.__dict__[name])]

parser = argparse.ArgumentParser(description='Proper ResNets for CIFAR10 in pytorch')
parser.add_argument('--arch', '-a', metavar='ARCH', default='resnet32',
                    choices=model_names,
                    help='model architecture: ' + ' | '.join(model_names) +
                    ' (default: resnet32)')
parser.add_argument('--workers', default=4, type=int, metavar='N',
                    help='number of data loading workers (default: 4)')
parser.add_argument('--epochs', default=200, type=int, metavar='N',
                    help='number of total epochs to run (default: 200)')
parser.add_argument('--resume', default=None, type=str, metavar='PATH',
                    help='path to latest checkpoint (default: None)')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=128, type=int,
                    metavar='N', help='mini-batch size (default: 128)')
parser.add_argument('--lr', '--learning-rate', default=0.1, type=float,
                    metavar='LR', help='initial learning rate (default: 0.1)')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum (default: 0.9)')
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')
parser.add_argument('-f', '--print-freq', default=50, type=int,
                    metavar='N', help='print frequency (default: 50)')
parser.add_argument('-e', '--evaluate', dest='evaluate', action='store_true',
                    help='whether to evaluate model on validation set')
parser.add_argument('--pretrained', dest='pretrained', action='store_true',
                    help='whether to use pre-trained model')
parser.add_argument('--amp', dest='amp', action='store_true',
                    help='whether to use automatic mixed precision (AMP) ')
parser.add_argument('--save-dir', dest='save_dir', default='save_temp', type=str,
                    help='directory used to save the trained models')
parser.add_argument('--save-every', dest='save_every', default=10, type=int,
                    help='save checkpoints every SAVE_EVERY epochs (default: 10)')
parser.add_argument('--gpu-id', default=0, type=int, help='GPU index (default: 0)')

best_prec1 = 0


def main():
    global args, best_prec1, writer, device
    args = parser.parse_args()
    writer = SummaryWriter('runs/' + args.arch)
    print(args)


    device = torch.device(f'cuda:{args.gpu_id}' if torch.cuda.is_available() else "cpu")
    
    # Check the save_dir exists or not
    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)

    model = resnet.__dict__[args.arch]()
    model.to(device)

    # optionally resume from a checkpoint
    if args.resume:
        if os.path.isfile(args.resume):
            print("=> loading checkpoint '{}'".format(args.resume))
            checkpoint = torch.load(args.resume, map_location=device)
            args.start_epoch = checkpoint['epoch']
            best_prec1 = checkpoint['best_prec1']
            model.load_state_dict(checkpoint['state_dict'])
            print("=> loaded checkpoint '{}' (epoch {})"
                  .format(args.resume, checkpoint['epoch']))
        else:
            print("=> no checkpoint found at '{}'".format(args.resume))

    cudnn.benchmark = True

    normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                     std=[0.229, 0.224, 0.225])

    train_loader = torch.utils.data.DataLoader(
        datasets.CIFAR10(root='./data', train=True, transform=transforms.Compose([
            transforms.RandomHorizontalFlip(),
            transforms.RandomCrop(32, 4),
            transforms.ToTensor(),
            normalize,
        ]), download=True),
        batch_size=args.batch_size, shuffle=True,
        num_workers=args.workers, pin_memory=True)

    val_loader = torch.utils.data.DataLoader(
        datasets.CIFAR10(root='./data', train=False, transform=transforms.Compose([
            transforms.ToTensor(),
            normalize,
        ])),
        batch_size=128, shuffle=False,
        num_workers=args.workers, pin_memory=True)

    # define loss function (criterion) and optimizer
    criterion = nn.CrossEntropyLoss().to(device)

    if args.amp:
        global scaler
        scaler = torch.cuda.amp.GradScaler()

    optimizer = torch.optim.SGD(model.parameters(), args.lr,
                                momentum=args.momentum,
                                weight_decay=args.weight_decay)

    lr_scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer,
                                                        milestones=[100, 150], last_epoch=args.start_epoch - 1)

    if args.arch in ['resnet1202', 'resnet110']:
        # for resnet1202 original paper uses lr=0.01 for first 400 minibatches for warm-up
        # then switch back. In this setup it will correspond for first epoch.
        for param_group in optimizer.param_groups:
            param_group['lr'] = args.lr*0.1


    if args.evaluate:
        validate(val_loader, model, criterion)
        return

    print(f"Start training at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    epoch_time = AverageMeter()
    train_time = AverageMeter()
    val_time = AverageMeter()
    for epoch in range(args.start_epoch, args.epochs):

        # train for one epoch
        start=time.time()
        print('Current learning rate: {:.5e}'.format(optimizer.param_groups[0]['lr']))
        writer.add_scalar('learning rate', optimizer.param_groups[0]['lr'], epoch)
        tmp = time.time()
        train(train_loader, model, criterion, optimizer, epoch)
        train_time.update(time.time()-tmp)
        print(f'Epoch {epoch} training time: {epoch_time.val} (average: {train_time.avg})')
        lr_scheduler.step()

        # evaluate on validation set
        tmp = time.time()
        prec1 = validate(val_loader, model, criterion, epoch)
        val_time.update(time.time()-tmp)
        print(f'Epoch {epoch} validation time: {epoch_time.val} (average: {val_time.avg})')

        # remember best prec@1 and save checkpoint
        is_best = prec1 > best_prec1
        best_prec1 = max(prec1, best_prec1)
        if is_best:
            torch.save({
                'epoch': epoch + 1,
                'state_dict': model.state_dict(),
                'best_prec1': best_prec1,
            }, os.path.join(args.save_dir, f'best_checkpoint.th'))

        # save checkpoint regularly
        if epoch > 0 and (epoch + 1) % args.save_every == 0:
            print(f"Saving checkpoint at epoch {epoch}...")
            torch.save({
                'epoch': epoch + 1,
                'state_dict': model.state_dict(),
                'best_prec1': best_prec1,
            }, os.path.join(args.save_dir, f'checkpoint_{epoch+1}.th'))


        epoch_time.update(time.time()-start)
        print(f'Epoch {epoch} validation time: {epoch_time.val} (average: {val_time.avg})')
        print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def train(train_loader, model, criterion, optimizer, epoch):
    """
        Run one train epoch
    """
    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    top1 = AverageMeter()

    # switch to train mode
    model.train()

    end = time.time()
    for i, (image, target) in enumerate(train_loader):

        # measure data loading time
        data_time.update(time.time() - end)

        target = target.to(device)
        input_var = image.to(device)
        target_var = target
        
        optimizer.zero_grad()

        if args.amp:
            # Casts operations to mixed precision
            with torch.cuda.amp.autocast():
                output = model(input_var)
                loss = criterion(output, target_var)
            # Scales the loss, and calls backward()
            # to create scaled gradients
            scaler.scale(loss).backward()

            # Unscales gradients and calls
            # or skips optimizer.step()
            scaler.step(optimizer)

            # Updates the scale for next iteration
            scaler.update()
        else:
            output = model(input_var)
            loss = criterion(output, target_var)

            # compute gradient and do SGD step
            loss.backward()
            optimizer.step()

        output = output.float()
        loss = loss.float()
        # measure accuracy and record loss
        prec1 = accuracy(output.data, target)[0]
        losses.update(loss.item(), image.size(0))
        top1.update(prec1.item(), image.size(0))


        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        if i % args.print_freq == 0:
            print('Epoch: [{0}][{1}/{2}]\t'
                  'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                  'Data {data_time.val:.3f} ({data_time.avg:.3f})\t'
                  'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                  'Prec@1 {top1.val:.3f} ({top1.avg:.3f})'.format(
                      epoch, i, len(train_loader), batch_time=batch_time,
                      data_time=data_time, loss=losses, top1=top1))

    writer.add_scalar('training loss', losses.avg, epoch)
    writer.add_scalar('training accuracy', top1.avg, epoch)


def validate(val_loader, model, criterion, epoch=None):
    """
    Run evaluation
    """
    batch_time = AverageMeter()
    losses = AverageMeter()
    top1 = AverageMeter()

    # switch to evaluate mode
    model.eval()

    end = time.time()
    with torch.no_grad():
        for i, (image, target) in enumerate(val_loader):
            target = target.to(device)
            input_var = image.to(device)
            target_var = target

            # if args.amp:
            #     input_var = input_var.half()

            # compute output
            output = model(input_var)
            loss = criterion(output, target_var)

            output = output.float()
            loss = loss.float()

            # measure accuracy and record loss
            prec1 = accuracy(output.data, target)[0]
            losses.update(loss.item(), image.size(0))
            top1.update(prec1.item(), image.size(0))


            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            if i % args.print_freq == 0:
                print('Test: [{0}/{1}]\t'
                      'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                      'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                      'Prec@1 {top1.val:.3f} ({top1.avg:.3f})'.format(
                          i, len(val_loader), batch_time=batch_time, loss=losses,
                          top1=top1))

    print(' * Prec@1 {top1.avg:.3f}'
          .format(top1=top1))
    writer.add_scalar('test loss', losses.avg, epoch)
    writer.add_scalar('test accuracy', top1.avg, epoch)

    return top1.avg

class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def accuracy(output, target, topk=(1,)):
    """Computes the precision@k for the specified values of k"""
    maxk = max(topk)
    batch_size = target.size(0)

    _, pred = output.topk(maxk, 1, True, True)
    pred = pred.t()
    correct = pred.eq(target.view(1, -1).expand_as(pred))

    res = []
    for k in topk:
        correct_k = correct[:k].view(-1).float().sum(0)
        res.append(correct_k.mul_(100.0 / batch_size))
    return res


if __name__ == '__main__':
    main()
