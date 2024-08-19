# IPTV

IPTV相关，适合个性化自定义...

* 自动收集整理
* 优先使用高频的直播源
* 优化的EPG文件尺寸，过滤掉直播源中不存在的频道
* 自动生成 [dist](https://github.com/JinnLynn/iptv/tree/dist)

## 使用

### 直接调用

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/live.m3u
```

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/live.txt
```

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/live-ipv4.m3u
```

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/live-ipv4.txt
```

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/epg.xml
```

```txt
https://raw.githubusercontent.com/JinnLynn/iptv/dist/epg.xml.gz
```

*注意: EPG为了减少文件大小，已经过处理，仅包含`channel.txt`中的频道（也就是生成的直播源中所包含的频道）数据，因此不通用，应与本项目生成的直播源文件配合使用*

### 手动生成

```shell
pip install -r requirements.txt
# m3u txt
python iptv.py
# epg
python epg.py
```

## 其它

* 直播源来自网络收集
* EPG来自 http://epg.51zmt.top:8000/
* 台标大部分来自 https://github.com/wanglindl/TVlogo
