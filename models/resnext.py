import torch.nn as nn
import torch.nn.functional as F
from torch.nn import init
import math
from .spatial_transformer_network import Localise
from .spatial_transformer_network import STN
import torch      
import numpy as np
from neupeak.utils import webcv2 as cv2
class ResNeXtBottleneck(nn.Module):
  expansion = 4
  """
  RexNeXt bottleneck type C (https://github.com/facebookresearch/ResNeXt/blob/master/models/resnext.lua)
  """
  def __init__(self, inplanes, planes, cardinality, base_width, stride=1, downsample=None):
    super(ResNeXtBottleneck, self).__init__()

    D = int(math.floor(planes * (base_width/64.0)))
    C = cardinality

    self.conv_reduce = nn.Conv2d(inplanes, D*C, kernel_size=1, stride=1, padding=0, bias=False)
    self.bn_reduce = nn.BatchNorm2d(D*C)

    self.conv_conv = nn.Conv2d(D*C, D*C, kernel_size=3, stride=stride, padding=1, groups=cardinality, bias=False)
    self.bn = nn.BatchNorm2d(D*C)

    self.conv_expand = nn.Conv2d(D*C, planes*4, kernel_size=1, stride=1, padding=0, bias=False)
    self.bn_expand = nn.BatchNorm2d(planes*4)

    self.downsample = downsample

  def forward(self, x):
    residual = x

    bottleneck = self.conv_reduce(x)
    bottleneck = F.relu(self.bn_reduce(bottleneck), inplace=True)

    bottleneck = self.conv_conv(bottleneck)
    bottleneck = F.relu(self.bn(bottleneck), inplace=True)

    bottleneck = self.conv_expand(bottleneck)
    bottleneck = self.bn_expand(bottleneck)

    if self.downsample is not None:
      residual = self.downsample(x)
    
    return F.relu(residual + bottleneck, inplace=True)

class ResNeXtdescriptor(nn.Module):
    def __init__(self, block, layers, cardinality, base_width):
        super(ResNeXtdescriptor, self).__init__()

        self.cardinality = cardinality
        self.base_width = base_width
        self.inplanes = 64
        self.conv1 = nn.Conv2d(3, 64, 7, 2, 3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.maxpool1 = nn.MaxPool2d(kernel_size=3, stride=2, padding= 1)
        self.stage_1 = self._make_layer(block, 64, layers[0])
        self.stage_2 = self._make_layer(block, 128, layers[1], 2)
        self.stage_3 = self._make_layer(block, 256, layers[2], 2)
        self.stage_4 = self._make_layer(block, 512, layers[3], 2)
        
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.Linear):
                init.kaiming_normal(m.weight)
                m.bias.data.zero_()
  
    def _make_layer(self, block, planes, blocks, stride= 1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                    nn.Conv2d(self.inplanes, planes* block.expansion, kernel_size=1, stride =stride, bias = False),

                    nn.BatchNorm2d(planes* block.expansion)
                    )
        layers = []
        layers.append(block(self.inplanes, planes, self.cardinality, self.base_width, stride, downsample))
        self.inplanes = planes* block.expansion

        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes, self.cardinality, self.base_width))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = F.relu(self.bn1(x), inplace=True)
        x = self.maxpool1(x)
        x = self.stage_1(x)
        x = self.stage_2(x)
        x = self.stage_3(x)
        x = self.stage_4(x)

        return x   

class SpatialTransformResNeXt(nn.Module):
    """
    many full resnext50s

    localiser, first_transformed, second_transformed,...,
    """
    def __init__(self, block,layers, cardinality, base_width, num_classes, 
            num_transformers, out_size, use_448px= False
            ):
        super(SpatialTransformResNeXt, self).__init__()
        self.N = num_transformers
        self.out_size = out_size
        self.use_448px = use_448px
        self.loc = nn.Sequential(
                ResNeXtdescriptor(block, layers, cardinality, base_width),
                Localise(inplanes=512* block.expansion, num_transformers= self.N)
                )
        self.stn = STN(self.out_size)

        self.crop_descriptors = nn.ModuleList([])
        for i in range(self.N):
            self.crop_descriptors.append(
                    nn.Sequential(
                        ResNeXtdescriptor(block, layers, cardinality, base_width),
                        nn.AvgPool2d(7)
                        )
                    )
        self.classifier = nn.Linear(self.N*512* block.expansion, num_classes)
        init.kaiming_normal(self.classifier.weight)
        self.classifier.bias.data.zero_()
        
        for name, m in self.named_modules():
            print(name)
    def forward(self, x):
        if self.use_448px:
            pass # do downscale (nearest or bilinear)
        thetas = self.loc(x)
        features = []
        for i in range(self.N):
            transformed = self.stn(x, thetas[i])
           # o = np.transpose((x.cpu().data.numpy()* 255)[0], (1,2,0))
           # img = np.transpose((transformed.cpu().data.numpy()* 255)[0], (1,2,0))
           # cv2.imshow('original', o)
           # cv2.imshow('transformed', img)
           # cv2.waitKey(0)
            feature = self.crop_descriptors[i](transformed)
            features.append(feature.view(feature.size(0),-1 ))
        x = torch.cat(features, 1)
        x = self.classifier(x)

        return x        

class CaltechBirdResNeXt(nn.Module):
    """
    ResNeXt50 for caltech birds classification
    """
    def __init__(self, block, layers, cardinality, base_width, num_classes):
        super(CaltechBirdResNeXt, self).__init__()

        self.cardinality = cardinality
        self.base_width = base_width
        self.num_classes = num_classes

        self.inplanes = 64

        self.conv1 = nn.Conv2d(3, 64, 7, 2, 3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)

        self.maxpool1 = nn.MaxPool2d(kernel_size=3, stride=2, padding= 1)

        self.stage_1 = self._make_layer(block, 64, layers[0])
        self.stage_2 = self._make_layer(block, 128, layers[1], 2)
        self.stage_3 = self._make_layer(block, 256, layers[2], 2)
        self.stage_4 = self._make_layer(block, 512, layers[3], 2)
        
        self.avgpool = nn.AvgPool2d(7)
        self.fc = nn.Linear(512* block.expansion, num_classes)
        
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.Linear):
                init.kaiming_normal(m.weight)
                m.bias.data.zero_()
  
    def _make_layer(self, block, planes, blocks, stride= 1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                    nn.Conv2d(self.inplanes, planes* block.expansion, kernel_size=1, stride =stride, bias = False),

                    nn.BatchNorm2d(planes* block.expansion)
                    )
        layers = []
        layers.append(block(self.inplanes, planes, self.cardinality, self.base_width, stride, downsample))
        self.inplanes = planes* block.expansion

        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes, self.cardinality, self.base_width))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = F.relu(self.bn1(x), inplace=True)
        x = self.maxpool1(x)
        x = self.stage_1(x)
        x = self.stage_2(x)
        x = self.stage_3(x)
        x = self.stage_4(x)
        x = self.avgpool(x)
        x = x.view(x.size(0), -1)

        return self.fc(x)

class CifarResNeXt(nn.Module):
  """
  ResNext optimized for the Cifar dataset, as specified in
  https://arxiv.org/pdf/1611.05431.pdf
  """
  def __init__(self, block, depth, cardinality, base_width, num_classes):
    super(CifarResNeXt, self).__init__()

    #Model type specifies number of layers for CIFAR-10 and CIFAR-100 model
    assert (depth - 2) % 9 == 0, 'depth should be one of 29, 38, 47, 56, 101'
    layer_blocks = (depth - 2) // 9

    self.cardinality = cardinality
    self.base_width = base_width
    self.num_classes = num_classes

    self.conv_1_3x3 = nn.Conv2d(3, 64, 3, 1, 1, bias=False)
    self.bn_1 = nn.BatchNorm2d(64)

    self.inplanes = 64
    self.stage_1 = self._make_layer(block, 64 , layer_blocks, 1)
    self.stage_2 = self._make_layer(block, 128, layer_blocks, 2)
    self.stage_3 = self._make_layer(block, 256, layer_blocks, 2)
    self.avgpool = nn.AvgPool2d(8)
    self.classifier = nn.Linear(256*block.expansion, num_classes)

    for m in self.modules():
      if isinstance(m, nn.Conv2d):
        n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
        m.weight.data.normal_(0, math.sqrt(2. / n))
      elif isinstance(m, nn.BatchNorm2d):
        m.weight.data.fill_(1)
        m.bias.data.zero_()
      elif isinstance(m, nn.Linear):
        init.kaiming_normal(m.weight)
        m.bias.data.zero_()

  def _make_layer(self, block, planes, blocks, stride=1):
    downsample = None
    if stride != 1 or self.inplanes != planes * block.expansion:
      downsample = nn.Sequential(
        nn.Conv2d(self.inplanes, planes * block.expansion,
              kernel_size=1, stride=stride, bias=False),
        nn.BatchNorm2d(planes * block.expansion),
      )

    layers = []
    layers.append(block(self.inplanes, planes, self.cardinality, self.base_width, stride, downsample))
    self.inplanes = planes * block.expansion
    for i in range(1, blocks):
      layers.append(block(self.inplanes, planes, self.cardinality, self.base_width))

    return nn.Sequential(*layers)

  def forward(self, x):
    x = self.conv_1_3x3(x)
    x = F.relu(self.bn_1(x), inplace=True)
    x = self.stage_1(x)
    x = self.stage_2(x)
    x = self.stage_3(x)
    x = self.avgpool(x)
    x = x.view(x.size(0), -1)
    return self.classifier(x)

def spatial_transform_resnext50(num_classes=200):
    out_size =(224,224)
    model = SpatialTransformResNeXt(ResNeXtBottleneck, [3,4,6,3], 32, 4, num_classes, 1,out_size )
    return model

def resnext50_32_4(num_classes=200):
    """
    resnext50 for caltech birds classification
    """
    model = CaltechBirdResNeXt(ResNeXtBottleneck, [3,4,6,3], 32, 4, num_classes)
    return model

def resnext29_16_64(num_classes=10):
  """Constructs a ResNeXt-29, 16*64d model for CIFAR-10 (by default)
  
  Args:
    num_classes (uint): number of classes
  """
  model = CifarResNeXt(ResNeXtBottleneck, 29, 16, 64, num_classes)
  return model

def resnext29_8_64(num_classes=10):
  """Constructs a ResNeXt-29, 8*64d model for CIFAR-10 (by default)
  
  Args:
    num_classes (uint): number of classes
  """
  model = CifarResNeXt(ResNeXtBottleneck, 29, 8, 64, num_classes)
  return model
