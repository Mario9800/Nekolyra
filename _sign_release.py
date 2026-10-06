# -*- coding: utf-8 -*-
r"""给发布的 SHA256SUMS.txt 签名。

用法：
    python _sign_release.py <SHA256SUMS.txt 路径>
会生成同名 .sig 文件（hex 文本），发布时两个一起传。

为什么单独一个脚本、私钥单独一个文件：
  这是"能改 GitHub 发布"之外的**第二把钥匙**。私钥不进仓库、不上传，
  所以拿到 GitHub token 的人换得了包、换不了签名。
  反过来，跑这个脚本的机器被入侵就全完了 —— 私钥文件请只放在自己机器上。

私钥丢了怎么办：
  用"直连"下载一版新的（旧版 app 不验签），把新公钥编进去再发一版。
  已经升到验签版本的用户，在拿到新公钥之前无法用加速节点。
"""
import io
import os
import sys

KEY = r"D:\ai\nekoe-release-key.pem"

if len(sys.argv) < 2:
    print("用法: python _sign_release.py <SHA256SUMS.txt>")
    sys.exit(1)

target = sys.argv[1]
if not os.path.exists(target):
    print("找不到 %s" % target)
    sys.exit(1)
if not os.path.exists(KEY):
    print("找不到私钥 %s" % KEY)
    print("（私钥不在仓库里，需要你本机有这个文件）")
    sys.exit(1)

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

key = serialization.load_pem_private_key(io.open(KEY, "rb").read(), password=None)
if not isinstance(key, Ed25519PrivateKey):
    print("这个私钥不是 Ed25519")
    sys.exit(1)

data = io.open(target, "rb").read()
sig = key.sign(data)
out = target + ".sig"
io.open(out, "w", encoding="ascii", newline="\n").write(sig.hex() + "\n")

# 立刻自检，免得签了个错的还不知道
pub = key.public_key()
pub.verify(sig, data)
raw = pub.public_bytes(encoding=serialization.Encoding.Raw,
                       format=serialization.PublicFormat.Raw)
print("  已签名 %s（%d 字节）" % (os.path.basename(target), len(data)))
print("  签名 -> %s（%d 字节 hex）" % (os.path.basename(out), len(sig.hex())))
print("  公钥指纹 %s…" % raw.hex()[:16])
print("  ✅ 自检通过（签完立刻验了一次）")

# 顺带确认公钥跟 bot.py 里编的是同一个 —— 不一致的话用户会全部验签失败
bot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")
if os.path.exists(bot):
    import re
    t = io.open(bot, encoding="utf-8").read()
    m = re.search(r'RELEASE_PUBKEY\s*=\s*"([0-9a-f]+)"', t)
    if m:
        if m.group(1) == raw.hex():
            print("  ✅ 跟 bot.py 里编的公钥一致")
        else:
            print("  ❌ bot.py 里的公钥是 %s…，跟这把私钥不配对！" % m.group(1)[:16])
            print("     用户会全部验签失败。先把公钥换过来再发版。")
            sys.exit(1)
