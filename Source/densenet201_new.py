import copy
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import torch
import torchvision
import torchvision.transforms as transforms
import torch.optim as optim
from MURAdataset import _load_model, clip, Focal_Loss, MURAdataset, per_class_dataset, sigmoid_test
import numpy as np
import os
import sys
import argparse
import time


def str2bool(v):
    if v.lower() in ['yes', 'true', 't', 'y', '1']:
        return True
    if v.lower() in ['no', 'false', 'f', 'n', '0']:
        return False
    else:
        raise argparse.ArgumentTypeError('Unsupported value encountered.')


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch_size', type=int, default=8)
    parser.add_argument('--epoch', type=int, default=40)
    parser.add_argument('--learning_rate', type=float, default=0.0001)
    parser.add_argument('--drop_rate', type=float, default=0)
    parser.add_argument('--load_model', type=str2bool, default=False)
    parser.add_argument('--load_path', type=str)
    parser.add_argument('--net_name', type=str)
    parser.add_argument('--save_path', type=str)
    parser.add_argument('--loss_type', type=str, default="logloss")
    return parser.parse_args()


# def model_modify(net, channels1, channels2):
#     net.classifier = nn.Linear(channels2, 1)
#     return net


if __name__ == '__main__':
    args = parse_args()
    for k, v in vars(args).items():
        print(k, v)

    batch_size = args.batch_size
    epoch = args.epoch
    learning_rate = args.learning_rate
    drop_rate = args.drop_rate
    load_model = args.load_model
    if load_model:
        load_path = args.load_path
    net_name = args.net_name
    save_path = args.save_path
    if os.path.exists(save_path) == False:
        os.makedirs(save_path)
    save_path += net_name + '.pkl'
    loss_type = args.loss_type

    train_transform = transforms.Compose([transforms.Resize([320, 320]),
                                          transforms.CenterCrop(224),
                                          transforms.RandomHorizontalFlip(),
                                          transforms.RandomRotation(30),
                                          transforms.ToTensor(),
                                          transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
                                          ])
    test_transform = transforms.Compose([transforms.Resize((320, 320)),
                                         transforms.CenterCrop(224),
                                         transforms.ToTensor(),
                                         transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
                                         ])

    present_dir_path = os.getcwd()

    trainset = per_class_dataset(csv_file=r'/train.csv', root_dir=present_dir_path,
                                 transform=train_transform, RGB=True)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=batch_size,
                                              shuffle=True, num_workers=4)

    tempList = np.load('./result/easy_to_hard.npy')
    isampler = torch.utils.data.sampler.SubsetRandomSampler(tempList[-8000:])
    trainloader1 = torch.utils.data.DataLoader(trainset, batch_size=batch_size, sampler=isampler, num_workers=4)

    testset = per_class_dataset(csv_file='/valid.csv', root_dir=present_dir_path,
                                transform=test_transform, RGB=True)
    testloader = torch.utils.data.DataLoader(testset, batch_size=batch_size,
                                             shuffle=False, num_workers=1)

    net = torchvision.models.densenet201(pretrained=not load_model)
    net.classifier = nn.Linear(1920, 1)

    optimizer = torch.optim.Adam(net.parameters(), lr=learning_rate)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', patience=20, verbose=True)

    if torch.cuda.device_count() > 1:
        print("Let's use GPUS!")
        net = nn.DataParallel(net)

    if torch.cuda.is_available():
        net = net.cuda()

    # for k, v in net.state_dict().iteritems():
    #     print("Layer {}".format(k))
    #     print(v)

    if load_model:
        print("load from %s" % load_path)
        net.load_state_dict(torch.load(load_path))

    # for k, v in net.state_dict().iteritems():
    #     print("Layer {}".format(k))
    #     print(v)

    best_score = 0

    for step in range(epoch):
        print ("(HARD) epoch %d" % step)
        t0 = time.time()
        epoch_loss = 0.0
        running_loss = 0.0
        total = 0
        net.train()
        for i, data in enumerate(trainloader1, 0):

            total += 1

            inputs, labels, weights = data
            weights = weights.type(torch.FloatTensor)
            labels = labels.type(torch.FloatTensor)
            inputs, labels, weights = Variable(inputs.cuda()), Variable(labels.cuda()), Variable(weights.cuda())
            optimizer.zero_grad()
            outputs = net(inputs).double()
            outputs = torch.sigmoid(net(inputs))
            outputs = outputs.select(1, 0)
            outputs = torch.clamp(outputs, min=1e-7, max=1 - 1e-7)
            if loss_type == "focalloss":
                loss = -((1 - outputs) * labels * outputs.log() + outputs * (1 - labels) * (1 - outputs).log())
            if loss_type == "logloss":
                loss = -(labels * outputs.log() + (1 - labels) * (1 - outputs).log())

            loss = (loss * weights).sum()
            # loss = loss.sum()
            loss.backward()
            optimizer.step()

            running_loss += loss.data.item()
            epoch_loss += loss.data.item()
            if i % 500 == 499:
                print('[%d, %5d] loss: %.3f' % (step + 1, i + 1, running_loss / 500))
                sys.stdout.flush()
                running_loss = 0.0
        if step % 1 == 0:
            net.eval()
            test_score, test_acc, test_loss = sigmoid_test(net, testloader)
            # scheduler.step(test_loss)
            print('test_score, test_accuracy and loss in epoch %d : %.3f %.3f %.3f [%3.fs]' % (
                step, test_score, test_acc, test_loss, time.time() - t0))
            # print('epoch_loss in epoch %d : %.3f' % (step, epoch_loss / total))
            sys.stdout.flush()
            if test_score > best_score:
                print("model saved!")
                best_score = test_score
                torch.save(net.state_dict(), save_path)

    for step in range(epoch):
        print ("epoch %d" % step)
        t0 = time.time()
        epoch_loss = 0.0
        running_loss = 0.0
        total = 0
        net.train()
        for i, data in enumerate(trainloader, 0):

            total += 1

            inputs, labels, weights = data
            weights = weights.type(torch.FloatTensor)
            labels = labels.type(torch.FloatTensor)
            inputs, labels, weights = Variable(inputs.cuda()), Variable(labels.cuda()), Variable(weights.cuda())
            optimizer.zero_grad()
            outputs = net(inputs).double()
            outputs = torch.sigmoid(net(inputs))
            outputs = outputs.select(1, 0)
            outputs = torch.clamp(outputs, min=1e-7, max=1 - 1e-7)
            if loss_type == "focalloss":
                loss = -((1 - outputs) * labels * outputs.log() + outputs * (1 - labels) * (1 - outputs).log())
            if loss_type == "logloss":
                loss = -(labels * outputs.log() + (1 - labels) * (1 - outputs).log())

            loss = (loss * weights).sum()
            # loss = loss.sum()
            loss.backward()
            optimizer.step()

            running_loss += loss.data.item()
            epoch_loss += loss.data.item()
            if i % 500 == 499:
                print('[%d, %5d] loss: %.3f' % (step + 1, i + 1, running_loss / 500))
                sys.stdout.flush()
                running_loss = 0.0
        if step % 1 == 0:
            net.eval()
            test_score, test_acc, test_loss = sigmoid_test(net, testloader)
            # scheduler.step(test_loss)
            print('test_score, test_accuracy and loss in epoch %d : %.3f %.3f %.3f [%3.fs]' % (
                step, test_score, test_acc, test_loss, time.time() - t0))
            # print('epoch_loss in epoch %d : %.3f' % (step, epoch_loss / total))
            sys.stdout.flush()
            if test_score > best_score:
                print("model saved!")
                best_score = test_score
                torch.save(net.state_dict(), save_path)

    print('Finished training and start to test')
    if best_score > 0:
        print("load from best score model")
        net.load_state_dict(torch.load(save_path))
    net.eval()
    test_score, test_acc, test_loss = sigmoid_test(net, testloader)
    print('final test_score, test_accuracy and loss: %.3f %.3f %.3f' % (test_score, test_acc, test_loss))
    print('Finished saving')
