"""Load an SRVGGNetCompact .pth and trace it to TorchScript for pnnx.

    python trace.py <model.pth> <num_feat> <num_conv> <out.pt>
"""
import sys
import torch
from srvgg import SRVGGNetCompact

pth, nf, nc, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
sd = torch.load(pth, map_location="cpu", weights_only=True)
sd = sd.get("params", sd.get("params_ema", sd))

net = SRVGGNetCompact(num_feat=nf, num_conv=nc, upscale=2)
missing, unexpected = net.load_state_dict(sd, strict=False)
assert not missing and not unexpected, (missing[:3], unexpected[:3])
net.eval()

torch.jit.trace(net, torch.rand(1, 3, 120, 160)).save(out)
print("traced ->", out)
