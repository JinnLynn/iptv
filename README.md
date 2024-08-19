## IPTV

自动收集整理，优先使用高频的直播源

### 使用

资源每天自动生成，存放于[dist](https://github.com/JinnLynn/iptv/tree/dist)分支

#### 直接调用

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/live.m3u
```

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/live.txt
```

仅包含IPv4地址:

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/live-ipv4.m3u
```

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/live-ipv4.txt
```

#### 手动生成

```shell
pip install -r requirements.txt
python iptv.py
```

#### 其它

* 直播源来自网络
* 台标大部分来自 https://github.com/wanglindl/TVlogo
