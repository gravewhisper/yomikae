# Adapted from saicinpainting/training/modules/ffc.py and saicinpainting/training/trainers/default.py
# in advimman/lama, Copyright 2021 Samsung Research, Apache License 2.0 (see server/licenses/LaMa-Apache-2.0.txt).
# Changed: kept only the big-lama generator path, and run the FFT in float32 under autocast.
from __future__ import annotations

from pathlib import Path

import torch
import torch.nn as nn

INPUT_NC = 4
OUTPUT_NC = 3
NGF = 64
N_DOWNSAMPLING = 3
N_BLOCKS = 18
MAX_FEATURES = 1024
RATIO_G = 0.75


class FourierUnit(nn.Module):
    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.conv_layer = nn.Conv2d(in_channels * 2, out_channels * 2, kernel_size=1, stride=1, padding=0, bias=False)
        self.bn = nn.BatchNorm2d(out_channels * 2)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        batch = x.shape[0]
        x = x.float()
        ffted = torch.fft.rfftn(x, dim=(-2, -1), norm="ortho")
        ffted = torch.stack((ffted.real, ffted.imag), dim=-1).permute(0, 1, 4, 2, 3).contiguous()
        ffted = ffted.view((batch, -1) + ffted.size()[3:])
        ffted = self.relu(self.bn(self.conv_layer(ffted)))
        ffted = ffted.view((batch, -1, 2) + ffted.size()[2:]).permute(0, 1, 3, 4, 2).contiguous().float()
        ffted = torch.complex(ffted[..., 0], ffted[..., 1])
        return torch.fft.irfftn(ffted, s=x.shape[-2:], dim=(-2, -1), norm="ortho")


class SpectralTransform(nn.Module):
    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.conv1 = nn.Sequential(
            nn.Conv2d(in_channels, out_channels // 2, kernel_size=1, bias=False),
            nn.BatchNorm2d(out_channels // 2),
            nn.ReLU(inplace=True),
        )
        self.fu = FourierUnit(out_channels // 2, out_channels // 2)
        self.conv2 = nn.Conv2d(out_channels // 2, out_channels, kernel_size=1, bias=False)

    def forward(self, x):
        x = self.conv1(x)
        return self.conv2(x + self.fu(x))


class FFC(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, ratio_gin, ratio_gout, stride=1, padding=0):
        super().__init__()
        in_cg = int(in_channels * ratio_gin)
        in_cl = in_channels - in_cg
        out_cg = int(out_channels * ratio_gout)
        out_cl = out_channels - out_cg
        self.ratio_gout = ratio_gout

        def conv(cin, cout):
            if cin == 0 or cout == 0:
                return nn.Identity()
            return nn.Conv2d(cin, cout, kernel_size, stride, padding, bias=False, padding_mode="reflect")

        self.convl2l = conv(in_cl, out_cl)
        self.convl2g = conv(in_cl, out_cg)
        self.convg2l = conv(in_cg, out_cl)
        self.convg2g = nn.Identity() if in_cg == 0 or out_cg == 0 else SpectralTransform(in_cg, out_cg)

    def forward(self, x):
        x_l, x_g = x if type(x) is tuple else (x, 0)
        out_xl, out_xg = 0, 0
        if self.ratio_gout != 1:
            out_xl = self.convl2l(x_l) + self.convg2l(x_g)
        if self.ratio_gout != 0:
            out_xg = self.convl2g(x_l) + self.convg2g(x_g)
        return out_xl, out_xg


class FFC_BN_ACT(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, ratio_gin, ratio_gout, stride=1, padding=0):
        super().__init__()
        self.ffc = FFC(in_channels, out_channels, kernel_size, ratio_gin, ratio_gout, stride, padding)
        global_channels = int(out_channels * ratio_gout)
        self.bn_l = nn.Identity() if ratio_gout == 1 else nn.BatchNorm2d(out_channels - global_channels)
        self.bn_g = nn.Identity() if ratio_gout == 0 else nn.BatchNorm2d(global_channels)
        self.act_l = nn.Identity() if ratio_gout == 1 else nn.ReLU(inplace=True)
        self.act_g = nn.Identity() if ratio_gout == 0 else nn.ReLU(inplace=True)

    def forward(self, x):
        x_l, x_g = self.ffc(x)
        return self.act_l(self.bn_l(x_l)), self.act_g(self.bn_g(x_g))


class FFCResnetBlock(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.conv1 = FFC_BN_ACT(dim, dim, 3, RATIO_G, RATIO_G, padding=1)
        self.conv2 = FFC_BN_ACT(dim, dim, 3, RATIO_G, RATIO_G, padding=1)

    def forward(self, x):
        id_l, id_g = x
        x_l, x_g = self.conv2(self.conv1(x))
        return id_l + x_l, id_g + x_g


class ConcatTupleLayer(nn.Module):
    def forward(self, x):
        x_l, x_g = x
        return torch.cat(x, dim=1) if torch.is_tensor(x_g) else x_l


class FFCResNetGenerator(nn.Module):
    def __init__(self):
        super().__init__()
        model = [nn.ReflectionPad2d(3), FFC_BN_ACT(INPUT_NC, NGF, 7, 0, 0)]
        for i in range(N_DOWNSAMPLING):
            mult = 2**i
            ratio_gout = RATIO_G if i == N_DOWNSAMPLING - 1 else 0
            model.append(FFC_BN_ACT(min(MAX_FEATURES, NGF * mult), min(MAX_FEATURES, NGF * mult * 2), 3, 0, ratio_gout, stride=2, padding=1))
        model += [FFCResnetBlock(min(MAX_FEATURES, NGF * 2**N_DOWNSAMPLING)) for _ in range(N_BLOCKS)]
        model.append(ConcatTupleLayer())
        for i in range(N_DOWNSAMPLING):
            mult = 2 ** (N_DOWNSAMPLING - i)
            out = min(MAX_FEATURES, int(NGF * mult / 2))
            model += [nn.ConvTranspose2d(min(MAX_FEATURES, NGF * mult), out, kernel_size=3, stride=2, padding=1, output_padding=1), nn.BatchNorm2d(out), nn.ReLU(True)]
        model += [nn.ReflectionPad2d(3), nn.Conv2d(NGF, OUTPUT_NC, kernel_size=7, padding=0), nn.Sigmoid()]
        self.model = nn.Sequential(*model)

    def forward(self, img: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        predicted = self.model(torch.cat([img * (1 - mask), mask], dim=1))
        return mask * predicted + (1 - mask) * img


def load(path: Path, device: str) -> FFCResNetGenerator:
    model = FFCResNetGenerator()
    model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True)["gen_state_dict"])
    return model.eval().to(device)
