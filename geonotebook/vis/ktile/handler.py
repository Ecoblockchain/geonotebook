from tornado import web, gen
import json
from notebook.base.handlers import IPythonHandler
from datetime import datetime, timedelta

from ModestMaps.Core import Coordinate
from jinja2 import Template
from .config import KtileConfig, KtileLayerConfig


def get_config():
    import gdal, osr

    map_srs = "+proj=merc +a=6378137 +b=6378137 +lat_ts=0.0 " + \
        "+lon_0=0.0 +x_0=0.0 +y_0=0.0 +k=1.0 +units=m " +\
        "+nadgrids=@null +wktext +no_defs +over"

    filepath = "/home/kotfic/src/geonotebook/notebooks/data/L57.Globe.month01.2009.hh09vv04.h6v1.doy005to031.NBAR.v3.0.tiff"

    raster = gdal.Open(filepath)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(raster.GetProjectionRef())
    layer_srs = srs.ExportToProj4()

    template = Template(open("/tmp/tmp.xml", "rb").read())

    mapnik_config = bytes(template.render(
        map_srs=map_srs, layer_srs=layer_srs,
        filepath=filepath
    ))



    config = {
        "cache":
        {
            "name": "Test",
            "path": "/tmp/stache",
            "umask": "0000"
        },
        "layers":
        {
            "osm":
            {
                "provider": {"name": "proxy", "provider": "OPENSTREETMAP"},
                "png options": {
                    "palette":
                    "http://tilestache.org/" +
                    "example-palette-openstreetmap-mapnik.act"
                }
            },
            "example":
            {
                "provider":
                {"name": "mapnik",
                 "mapconfig": "/home/kotfic/src/KTile/examples/style.xml"},
                "projection": "spherical mercator"
            },

            "raster2":
            {
                "provider": {"class": "geonotebook.vis.ktile.provider:MapnikPythonProvider",
                             "kwargs": {"path": filepath}}
            },

            "raster":
            {
                "provider":
                {"name": "mapnik",
                 "mapfile": mapnik_config}
            }
        }
    }

    return config


class KtileHandler(IPythonHandler):
    def initialize(self, ktile_config_manager):
        self.ktile_config_manager = ktile_config_manager
        self.ktile_config_manager.foo = id(ktile_config_manager)

    def post(self, kernel_id):
        self.ktile_config_manager[kernel_id] = KtileConfig()

    def delete(self, kernel_id):
        try:
            del self.ktile_config_manager[kernel_id]
        except KeyError:
            raise web.HTTPError(404, u'Kernel %s not found' % kernel_id)

    def get(self, kernel_id, **kwargs):
        config = self.ktile_config_manager[kernel_id].as_dict()
        try:
            self.finish(config)
        except KeyError:
            raise web.HTTPError(404, u'Kernel %s not found' % kernel_id)


class KtileLayerHandler(IPythonHandler):
    def initialize(self, ktile_config_manager):
        self.ktile_config_manager = ktile_config_manager

    def prepare(self):
        try:
            if self.request.headers["Content-Type"].startswith("application/json"):
                self.request.json = json.loads(self.request.body)
        except Exception:
            self.request.json = None

    def post(self, kernel_id, layer_name, **kwargs):
        try:
            filepath = self.request.json['path']
        except KeyError:
            raise web.HTTPError(500, '"path" not passed')

        try:
            self.ktile_config_manager[kernel_id][layer_name] = \
                KtileLayerConfig(
                    layer_name,
                    provider={
                        "class": "geonotebook.vis.ktile.provider:MapnikPythonProvider",
                        "kwargs": {"path": filepath }
                    })

        except KeyError:
            raise web.HTTPError(404, u'Kernel %s not found' % kernel_id)

        self.finish()

    def get(self, kernel_id, layer_name, **kwargs):
        config = self.ktile_config_manager[kernel_id].as_dict()
        try:
            self.finish(config)
        except KeyError:
            raise web.HTTPError(404, u'Kernel %s not found' % kernel_id)


from concurrent.futures import ThreadPoolExecutor
from tornado import concurrent, ioloop

class KTileAsyncClient(object):
    __instance = None

    def __new__(cls, *args, **kwargs):
        if cls.__instance is None:
            cls.__instance = super(
                KTileAsyncClient, cls).__new__(cls, *args, **kwargs)
        return cls.__instance

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.io_loop = ioloop.IOLoop.current()

    @concurrent.run_on_executor
    def getTileResponse(self, layer, coord, extension):
        return layer.getTileResponse(coord, extension)

class KtileTileHandler(IPythonHandler):

    def initialize(self, ktile_config_manager):
        self.client = KTileAsyncClient()
        self.ktile_config_manager = ktile_config_manager

    @gen.coroutine
    def get(self, kernel_id, layer_name, x, y, z, extension, **kwargs):
        config = self.ktile_config_manager[kernel_id].config


        layer = config.layers[layer_name]
        coord = Coordinate(int(y), int(x), int(z))

        status_code, headers, content = yield self.client.getTileResponse(
            layer, coord, extension)


        if layer.max_cache_age is not None:
            expires = datetime.utcnow() + timedelta(
                seconds=layer.max_cache_age)
            headers.setdefault(
                'Expires', expires.strftime('%a %d %b %Y %H:%M:%S GMT'))
            headers.setdefault(
                'Cache-Control', 'public, max-age=%d' % layer.max_cache_age)

        # Force allow cross origin access
        headers["Access-Control-Allow-Origin"] = "*"

        # Fill tornado handler properties with ktile code/header/content
        for k, v in headers.items():
            self.set_header(k, v)

        self.set_status(status_code)

        self.write(content)

# Debug Code


class GeoJSTestHandler(IPythonHandler):
    template = Template("""
    <head>
    <script src="/nbextensions/geonotebook/lib/geo.js"></script>


    <style>
        html, body, #map {
            margin: 0;
            width: 100%;
            height: 100%;
            overflow: hidden;
        }
    </style>

    <script>
    $(function () {
            var map = geo.map({'node': '#map'});
            var layer = map.createLayer('osm', {
'keepLower': false,
'url': 'http://192.168.30.110:8888/ktile/{{kernel_id}}/{{layer_name}}/{x}/{y}/{z}.png'
            });

    });
    </script>
    </head>
    <body>
    <div id="map"></div>
    </body>
    """)

    def get(self, kernel_id, layer_name, *args, **kwargs):
        self.finish(
            self.template.render({"layer_name": layer_name,
                                  "kernel_id": kernel_id})
        )