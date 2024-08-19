import os
import xml.etree.ElementTree as ET
import datetime
import gzip
from pprint import pprint
from io import StringIO, BytesIO

from iptv import IPTV, logging, conv_dict, clean_inline_comment

EPG_GZ_DISABLED = os.environ.get('EPG_GZ_DISABLED') or False
EPG_SOURCE = os.environ.get('EPG_SOURCE') or 'http://epg.51zmt.top:8000/e.xml.gz'
EPG_CHANNEL_MAP = os.environ.get('EPG_CHANNEL_MAP') or 'epg.txt'

_info_name_keys = ['generator-info-name', 'info-name', 'source-info-name']
_info_url_keys = ['generator-info-url', 'info-url', 'source-info-url']

class EPG:
    def __init__(self, *args, **kwargs):
        self.iptv = IPTV()
        self.iptv.load_channels()

        self.epg_doc = None

    def fetch_epg(self):
        url = EPG_SOURCE
        try:
            res = self.iptv.fetch(url)
            logging.info(f'EPG获取成功: {url}')
            try:
                content = gzip.decompress(res.content)
                logging.info('EPG解压成功')
            except:
                content = res.content
            self.epg_doc = ET.parse(BytesIO(content))
        except Exception as e:
            logging.error(f'解析EPG出错: {url} {e}')

    def load_channel_name_map(self):
        map_ = {}
        with open(EPG_CHANNEL_MAP) as fp:
            for line in fp.readlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                line = clean_inline_comment(line)
                map_.update(conv_dict(line))
        return map_

    def convert_channel_name(self):
        map_ = self.load_channel_name_map()
        root = self.epg_doc.getroot()
        for channel in root.findall('channel'):
            ele = channel.find('display-name')
            if ele.text in map_:
                logging.debug(f'映射频道名: {ele.text} => {map_[ele.text]}')
                ele.text = map_[ele.text]

    def cleanup(self):
        del_channel_ids = []
        reserved_channel_names = []
        root = self.epg_doc.getroot()
        for channel in root.findall('channel'):
            ele = channel.find('display-name')
            name = ele.text
            if name not in self.iptv.channels.keys():
                del_channel_ids.append(channel.get('id'))
                root.remove(channel)
            else:
                reserved_channel_names.append(name)

        for programme in root.findall('programme'):
            if programme.get('channel') in del_channel_ids:
                root.remove(programme)

        non_existed_channels = ', '.join([n for n in self.iptv.channels.keys() if n not in reserved_channel_names])
        logging.info(f'没有节目表的频道: {non_existed_channels}')

    def normalize_extras(self):
        def _existing_value(ele, try_keys):
            if not isinstance(try_keys, list):
                try_keys = [try_keys]
            for k in try_keys:
                if ele.get(k):
                    return root.get(k)

        def _normalize(n, u):
            # 51zmt name url 信息写反
            if 'epg.51zmt.top' in u or 'epg.51zmt.top' in n:
                n, u = u, n
            return n, u

        root = self.epg_doc.getroot()
        info_name = _existing_value(root, _info_name_keys)
        info_url = _existing_value(root, _info_url_keys)

        info_name, info_url = _normalize(info_name, info_url)

        root.attrib.clear()
        now = datetime.datetime.now(datetime.UTC)
        root.set('date', now.strftime('%Y%m%d%H%M%S +0000'))
        root.set('generator-info-name', 'JinnLynn/iptv')
        root.set('generator-info-url', 'https://github.com/JinnLynn/iptv')
        root.set('source-info-name', info_name)
        root.set('source-info-url', info_url or EPG_SOURCE)

    def normalize(self):
        self.convert_channel_name()
        self.cleanup()
        self.normalize_extras()

    def dumpb(self):
        root = self.epg_doc.getroot()
        ET.indent(root)
        return ET.tostring(root, encoding='utf-8', xml_declaration=True)

    def dumps(self):
        return self.dumpb().decode()

    def export_xml(self):
        dst = self.iptv.get_dist('epg.xml')
        with open(dst, 'w') as fp:
            fp.write(self.dumps())
        logging.info(f'导出xml: {dst}')

    def export_xml_gz(self):
        dst = self.iptv.get_dist('epg.xml.gz')
        with open(dst, 'wb') as fp:
            fp.write(gzip.compress(self.dumpb()))
        logging.info(f'导出xml.gz: {dst}')

    def run(self):
        self.fetch_epg()
        self.normalize()
        self.export_xml()

        if not EPG_GZ_DISABLED:
            self.export_xml_gz()


if __name__ == '__main__':
    epg = EPG()
    epg.run()
