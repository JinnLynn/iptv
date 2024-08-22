import os
from configparser import ConfigParser, NoOptionError
from collections import OrderedDict
import re
from urllib.parse import urlparse
import logging
import itertools
import typing as t
import json
from datetime import datetime
from itertools import islice

import requests
import zhconv

DEBUG = os.environ.get('DEBUG') is not None
IPTV_CONFIG = os.environ.get('IPTV_CONFIG') or 'config.ini'
IPTV_CHANNEL = os.environ.get('IPTV_CHANNEL') or 'channel.txt'
IPTV_DIST = os.environ.get('IPTV_DIST') or 'dist'
EXPORT_RAW = ConfigParser.BOOLEAN_STATES[os.environ.get('EXPORT_RAW', default=str(DEBUG)).lower()]
EXPORT_JSON = ConfigParser.BOOLEAN_STATES[os.environ.get('EXPORT_JSON', default=str(DEBUG)).lower()]

DEF_LINE_LIMIT = 10
DEF_REQUEST_TIMEOUT = 100
DEF_USER_AGENT = 'okhttp/4.12.0-iptv'
DEF_INFO_LINE = 'https://gcalic.v.myalicdn.com/gc/wgw05_1/index.m3u8?contentid=2820180516001'
DEF_EPG = 'https://raw.githubusercontent.com/JinnLynn/iptv/dist/epg.xml'
DEF_IPV4_FILENAME_SUFFIX = '-ipv4'
DEF_WHITELIST_PRIORITY = 10

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format='[%(asctime)s][%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()])

# REF: https://github.com/bustawin/ordered-set-37
T = t.TypeVar("T")
class OrderedSet(t.MutableSet[T]):
    __slots__ = ('_d',)

    def __init__(self, iterable: t.Optional[t.Iterable[T]] = None):
        self._d = dict.fromkeys(iterable) if iterable else {}

    def add(self, x: T) -> None:
        self._d[x] = None

    def clear(self) -> None:
        self._d.clear()

    def discard(self, x: T) -> None:
        self._d.pop(x, None)

    def __getitem__(self, index) -> T:
        try:
            return next(itertools.islice(self._d, index, index + 1))
        except StopIteration:
            raise IndexError(f"index {index} out of range")

    def __contains__(self, x: object) -> bool:
        return self._d.__contains__(x)

    def __len__(self) -> int:
        return self._d.__len__()

    def __iter__(self) -> t.Iterator[T]:
        return self._d.__iter__()

    def __str__(self):
        return f"{{{', '.join(str(i) for i in self)}}}"

    def __repr__(self):
        return f"<OrderedSet {self}>"

class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, set):
            return list(o)
        return super().default(o)

def json_dump(obj, fp=None, **kwargs):
    kwargs.setdefault('cls', JSONEncoder)
    kwargs.setdefault('indent', 2)
    kwargs.setdefault('ensure_ascii', False)
    return json.dump(obj, fp, **kwargs) if fp else json.dumps(obj, **kwargs)

def conv_bool(v):
    if isinstance(v, bool):
        return v
    return ConfigParser.BOOLEAN_STATES[v.lower()]

def conv_list(v):
    v = v.strip().splitlines()
    return [s.strip() for s in v if s.strip()]

def conv_dict(v):
    maps = {}
    for m in conv_list(v):
        s = re.split(r'\ +', m)
        if len(s) != 2:
            logging.error(f'字典配置错误: {m} => {s}')
            continue
        maps[s[0].strip()] = s[1].strip()
    return maps

def clean_inline_comment(v):
    def _remove_inline_comment(l):
        try:
            l = re.split(r' +#', l)[0]
        except Exception as e:
            logging.warning(f'行内注释清理出错: {l} {e}')
        return l
    return '\n'.join([_remove_inline_comment(s) for s in v.strip().splitlines()])

def is_ipv6(url):
    p = urlparse(url)
    return re.match(r'\[[0-9a-fA-F:]+\]', p.netloc) is not None

class IPTV:
    def __init__(self, *args, **kwargs):
        self._cate_logos = None
        self._channel_map = None
        self._blacklist = None
        self._whitelist = None

        self.raw_config = None
        self.raw_channels = {}
        self.channel_cates = OrderedDict()
        self.channels = {}

    def get_config(self, key, *convs, default=None):
        if not self.raw_config:
            self.raw_config = ConfigParser()
            self.raw_config.read([c.strip() for c in IPTV_CONFIG.split(',')])

        try:
            value = self.raw_config.get('config', key)
            value = clean_inline_comment(value)
            if convs:
                for conv in convs:
                    value = conv(value)
        except NoOptionError:
            # logging.debug(f'配置未设置, 返回默认值: {key} : {default}')
            return default
        except Exception as e:
            logging.error(f'获取配置出错: {key} {e}')
            return default
        return value

    def _get_path(self, dist, filename):
        if not os.path.isdir(dist):
            os.makedirs(dist, exist_ok=True)
        abspath = os.path.join(dist, filename)
        if not os.path.isdir(os.path.dirname(abspath)):
            os.makedirs(os.path.dirname(abspath), exist_ok=True)
        return abspath

    def get_dist(self, filename, ipv4_suffix=False):
        parts = filename.rsplit('.', 1)
        if ipv4_suffix:
            parts[0] = f'{parts[0]}{DEF_IPV4_FILENAME_SUFFIX}'
        return self._get_path(IPTV_DIST, '.'.join(parts))

    @property
    def cate_logos(self):
        if self._cate_logos is None:
            self._cate_logos = self.get_config('logo_cate', conv_dict, default={})
        return self._cate_logos

    @property
    def channel_map(self):
        if self._channel_map is None:
            self._channel_map = self.get_config('channel_map', conv_dict, default={})
        return self._channel_map

    @property
    def blacklist(self):
        if self._blacklist is None:
            self._blacklist = self.get_config('blacklist', conv_list, default=[])
        return self._blacklist

    @property
    def whitelist(self):
        if self._whitelist is None:
            self._whitelist = self.get_config('whitelist', conv_list, default=[])
        return self._whitelist

    def load_channels(self):
        for f in IPTV_CHANNEL.split(','):
            current = ''
            with open(f) as fp:
                for line in fp.readlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('CATE:'):
                        current = line[5:].strip()
                        self.channel_cates.setdefault(current, OrderedSet())
                    else:
                        if not current:
                            logging.warning(f'忽略没有指定分类的频道: {line}')
                            continue

                        if line.startswith('-'):
                            line = line[1:].strip()
                            if line in self.channel_cates[current]:
                                self.channel_cates[current].remove(line)
                        else:
                            self.channel_cates[current].add(line)

        for v in self.channel_cates.values():
            for c in v:
                self.channels.setdefault(c, [])

    def fetch(self, url):
        headers = {'User-Agent': DEF_USER_AGENT}
        res = requests.get(url, timeout=DEF_REQUEST_TIMEOUT, headers=headers)
        res.raise_for_status()
        return res

    def fetch_sources(self):
        sources = self.get_config('source', conv_list, default=[])
        success_count = 0
        failed_sources = []
        for url in sources:
            try:
                res = self.fetch(url)
            except Exception as e:
                logging.warning(f'获取失败: {url} {e}')
                failed_sources.append(url)
                continue
            is_m3u = any('#EXTINF' in l.decode() for l in islice(res.iter_lines(), 10))
            logging.info(f'获取成功: {"M3U" if is_m3u else "TXT"} {url}')
            success_count = success_count + 1

            cur_cate = None

            for line in res.iter_lines():
                line = line.decode().strip()
                if not line:
                    continue

                if is_m3u:
                    if line.startswith("#EXTINF"):
                        match = re.search(r'group-title="(.*?)",(.*)', line)
                        if match:
                            cur_cate = match.group(1).strip()
                            chl_name = match.group(2).strip()
                    elif not line.startswith("#"):
                        channel_url = line.strip()
                        self.add_channel_uri(chl_name, channel_url)
                else:
                    if "#genre#" in line:
                        cur_cate = line.split(",")[0].strip()
                    elif cur_cate:
                        match = re.match(r"^(.*?),(.*?)$", line)
                        if match:
                            chl_name = match.group(1).strip()
                            channel_url = match.group(2).strip()
                            self.add_channel_uri(chl_name, channel_url)
        logging.info(f'源读取完毕: 成功: {success_count} 失败: {len(failed_sources)}')
        if failed_sources:
            logging.warning(f'获取失败的源: {failed_sources}')
        self.stat_fetched_channels()

    def is_port_necessary(self, scheme, netloc):
        if netloc[-1] == ']':
            return False

        out = netloc.rsplit(":", 1)
        if len(out) == 1:
            return False
        else:
            try:
                port = int(out[1])
                if scheme == 'http' and port == 80:
                    return True
                if scheme == 'https' and port == 443:
                    return True
            except ValueError:
                return False
        return False

    def clean_channel_name(self, name):
        def re_subs(s, *reps):
            for rep in reps:
                r = ''
                c = 0
                if len(rep) == 1:
                    p = rep[0]
                elif len(rep) == 2:
                    p, r = rep
                else:
                    p, r, c = rep
                s = re.sub(p, r, s, c)
            return s
        def any_startswith(s, *args):
            return any([re.search(fr'^{r}', s, re.IGNORECASE) for r in args])

        def any_in(s, *args):
            return any(a in s for a in args)

        # 繁 => 简
        jap = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7A3]')  # \uAC00-\uD7A3为匹配韩文的，其余为日文
        if not jap.search(name):
            name = zhconv.convert(name, 'zh-cn', {'「': '「', '」': '」'})

        if name.startswith('CCTV'):
            name = re_subs(name,
                                (r'-[(HD)0]*', ),                           # CCTV-0 CCTV-HD
                                (r'(CCTV[1-9][0-9]?[\+K]?).*', r'\1')
            )
            # FIX:
            # CCTV4美洲 ... => CCTV4
        elif name.startswith('CETV'):
            name = re_subs(name,
                                (r'[ -][(HD)0]*', ),
                                (r'(CETV[1-4]).*', r'\1'),
            )
        elif any_startswith(name, 'NewTV', 'CHC', 'iHOT'):
            for p in ['NewTV', 'CHC', 'iHOT']:
                name = re.sub(fr'^{p}', p, name, 1, re.IGNORECASE)
                if not name.startswith(p):
                    continue
                name = re_subs(name,
                                    (re.compile(f'{p} +'), p, 1),
                                    (r'(.*) +.*', r'\1')
                )
        elif re.match(r'^TVB[^s]', name, re.IGNORECASE):
            name = name.replace(' ', '')
        return name

    def add_channel_for_debug(self, name, url, org_name, org_url):
        if name not in self.raw_channels:
            self.raw_channels.setdefault(name, OrderedDict(source_names=set(), source_urls=set(), lines=[]))

        self.raw_channels[name]['source_names'].add(org_name)
        self.raw_channels[name]['source_urls'].add(org_url)

        for u in self.raw_channels[name]['lines']:
            if u['uri'] == url:
                u['count'] += u['count'] + 1
                return
        self.raw_channels[name]['lines'].append({'uri': url, 'count': 1, 'ipv6': is_ipv6(url)})

    def try_map_channel_name(self, name):
        if name in self.channel_map.keys():
            o_name = name
            name = self.channel_map[name]
            logging.debug(f'映射频道名: {o_name} => {name}')
        return name

    def add_channel_uri(self, name, uri):
        uri = re.sub(r'\$.*$', '', uri)

        name = self.try_map_channel_name(name)

        # 处理频道名
        org_name = name
        name = self.clean_channel_name(name)
        if org_name != name:
            logging.debug(f'规范频道名: {org_name} => {name}')

        name = self.try_map_channel_name(name)

        changed = False
        p = urlparse(uri)
        try:
            if self.is_port_necessary(p.scheme, p.netloc):
                changed = True
                p = p._replace(netloc=p.netloc.rsplit(':', 1)[0])
        except Exception as e:
            logging.debug(f'频道线路地址出错: {name} {uri} {e}')
            return

        url = p.geturl() if changed else uri

        self.add_channel_for_debug(name, url, org_name, uri)

        if name not in self.channels:
            return

        if self.is_on_blacklist(url):
            logging.debug(f'黑名单忽略: {name} {uri}')
            return

        priority = DEF_WHITELIST_PRIORITY if self.is_on_whitelist(url) else 0
        for u in self.channels[name]:
            if u['uri'] == url:
                u['count'] = u['count'] + 1
                u['priority'] = u['count'] + priority
                return
        self.channels[name].append({'uri': url, 'priority': priority + 1, 'count': 1, 'ipv6': is_ipv6(url)})

        # if changed:
        #     logging.debug(f'URL cleaned: {uri} => \n                                              {p.geturl()}')

    def sort_channels(self):
        for k in self.channels:
            self.channels[k].sort(key=lambda i: i['priority'], reverse=True)

    def stat_fetched_channels(self):
        line_num = sum([len(c) for c in self.channels])
        logging.info(f'获取的所需: 频道: {len(self.channels)} 线路: {line_num}')
        # TODO: 输出没有获取到任何线路的频道

    def is_on_blacklist(self, url):
        # TODO: 支持regex
        return any(b in url for b in self.blacklist)
        # return any(re.search(re.compile(b), url) for b in self.blacklist)

    def is_on_whitelist(self, url):
        # TODO: 支持regex
        return any(b in url for b in self.whitelist)

    def enum_channel_uri(self, name, limit=None, only_ipv4=False):
        if name not in self.channels:
            return []
        if limit is None:
            limit = self.get_config('limit', int, default=DEF_LINE_LIMIT)
        index = 0
        for chl in self.channels[name]:
            if only_ipv4 and chl['ipv6']:
                continue
            index = index + 1
            if isinstance(limit, int) and limit > 0 and index > limit:
                return
            yield index, chl

    def export_info(self, fmt='m3u', fp=None):
        if self.get_config('disable_export_info', conv_bool, default=False):
            return
        day = datetime.now().strftime('%Y-%m-%d')
        url = DEF_INFO_LINE
        output = []

        if fmt == 'm3u':
            logo_url_prefix = self.get_config('logo_url_prefix', lambda s: s.rstrip('/'))
            output.append(f'#EXTINF:-1 tvg-id="1" tvg-name="{day}" tvg-logo="{logo_url_prefix}/default.png" group-title="更新信息",{day}')
            output.append(f'{url}')
        else:
            output.append('更新信息,#genre#')
            output.append(f'{day},{url}')

        output = '\n'.join(output)
        if fp:
            fp.write(output)
        return output

    def get_export_filename(self, filename, only_ipv4=False):
        parts = filename.rsplit('.', 1)
        if only_ipv4:
            parts[0] = f'{parts[0]}-ipv4'
        return '.'.join(parts)

    def export_m3u(self, only_ipv4=False):
        dst = self.get_dist('live.m3u', ipv4_suffix=only_ipv4)
        logo_url_prefix = self.get_config('logo_url_prefix', lambda s: s.rstrip('/'))

        with open(dst, 'w') as fp:
            fp.write('#EXTM3U x-tvg-url="{}"\n'.format(self.get_config('epg', default=DEF_EPG)))
            for cate, chls in self.channel_cates.items():
                for chl_name in chls:
                    for index, uri in self.enum_channel_uri(chl_name, only_ipv4=only_ipv4):
                        logo = self.cate_logos[cate] if cate in self.cate_logos else f'{chl_name}.png'
                        fp.write(f'#EXTINF:-1 tvg-id="{index}" tvg-name="{chl_name}" tvg-logo="{logo_url_prefix}/{logo}" group-title="{cate}",{chl_name}\n')
                        fp.write('{}\n'.format(uri['uri']))
            self.export_info(fmt='m3u', fp=fp)
        logging.info(f'导出M3U: {dst}')

    def export_txt(self, only_ipv4=False):
        dst = self.get_dist('live.txt', ipv4_suffix=only_ipv4)
        with open(dst, 'w') as fp:
            for cate, chls in self.channel_cates.items():
                fp.write(f'{cate},#genre#\n')
                for chl_name in chls:
                    for index, uri in self.enum_channel_uri(chl_name, only_ipv4=only_ipv4):
                        fp.write('{},{}\n'.format(chl_name, uri['uri']))
                fp.write('\n\n')
            self.export_info(fmt='txt', fp=fp)
        logging.info(f'导出TXT: {dst}')

    def export_json(self, only_ipv4=False):
        dst = self.get_dist('raw/channel.json', ipv4_suffix=only_ipv4)
        data = OrderedDict()
        for cate, chls in self.channel_cates.items():
            data.setdefault(cate, OrderedDict())
            for chl_name in chls:
                data[cate].setdefault(chl_name, [])
                for index, uri in self.enum_channel_uri(chl_name, only_ipv4=only_ipv4):
                    data[cate][chl_name].append(uri)
        with open(dst, 'w') as fp:
            json_dump(data, fp)
        logging.info(f'导出JSON: {dst}')

    def export_raw(self):
        dst = self.get_dist('raw/source.json')
        for k in self.raw_channels:
            self.raw_channels[k]['lines'].sort(key=lambda i: i['count'], reverse=True)
        with open(dst, 'w') as fp:
            json_dump(self.raw_channels, fp)
        logging.info(f'导出RAW: {dst}')

    def export(self):
        self.sort_channels()

        self.export_m3u()
        self.export_txt()

        if EXPORT_JSON:
            self.export_json()

        if self.get_config('export_ipv4_version', conv_bool, default=False):
            self.export_m3u(only_ipv4=True)
            self.export_txt(only_ipv4=True)
            if EXPORT_JSON:
                self.export_json(only_ipv4=True)

        if EXPORT_RAW:
            self.export_raw()

    def run(self):
        self.load_channels()
        self.fetch_sources()
        self.export()


if __name__ == '__main__':
    iptv = IPTV()
    iptv.run()
