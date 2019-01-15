import json
import os

from girder import events
from girder.api import access
from girder.api.describe import Description, autoDescribeRoute
from girder.api.rest import Resource, filtermodel
from girder.constants import AccessType, TokenScope
from girder.exceptions import RestException, ValidationException
from girder.models.collection import Collection
from girder.models.file import File
from girder.models.folder import Folder
from girder.models.item import Item
from girder.models.user import User
from girder.plugin import getPlugin, registerPluginWebroot, GirderPlugin
from girder_jobs.models.job import Job
from girder.utility import setting_utilities
from girder.utility.server import staticFile
from girder_worker.docker.tasks import docker_run
from girder_worker.docker.transforms import VolumePath
from girder_worker.docker.transforms.girder import (
    GirderItemIdToVolume, GirderUploadVolumePathToItem)

THUMB_SLICES = 11
THUMB_WIDTH = THUMB_HEIGHT = 256


class PluginSettings(object):
    STUDIES_COLL_ID = 'stroke.studies_collection_id'


def _handleUpload(event):
    upload, file = event.info['upload'], event.info['file']

    try:
        reference = json.loads(upload.get('reference'))
    except (TypeError, ValueError):
        return

    if isinstance(reference, dict) and 'interactive_thumbnail' in reference:
        item = Item().load(file['itemId'], force=True, exc=True)

        file['interactive_thumbnails_uid'] = file['name']
        file['attachedToId'] = item['_id']
        file['attachedToType'] = 'item'
        file['itemId'] = None
        File().save(file)

        if not item.get('hasInteractiveThumbnail'):
            Item().update({'_id': item['_id']}, {'$set': {
                'hasInteractiveThumbnail': True
            }}, multi=False)


def _removeThumbnails(item, saveItem=False):
    rm = File().remove

    for file in File().find({'attachedToId': item['_id']}):
        if 'interactive_thumbnails_uid' in file:
          rm(file)

    if saveItem:
        Item().update(
            {'_id': item['_id']},
            {'$set': {'hasInteractiveThumbnail': False}},
            multi=False)


class Study(Resource):
    def __init__(self):
        super(Study, self).__init__()
        self.resourceName = 'study'

        self.route('GET', (), self.listStudies)
        self.route('POST', (), self.createStudy)

    @access.public(scope=TokenScope.DATA_READ)
    @filtermodel(Folder)
    @autoDescribeRoute(
        Description('List studies.')
        .pagingParams(defaultSort='patientId', defaultLimit=500)
    )
    def listStudies(self, limit, offset, sort):
        cursor = Folder().find({'isStudy': True}, sort=sort)
        return list(Folder().filterResultsByPermission(
            cursor, level=AccessType.READ, user=self.getCurrentUser(), limit=limit, offset=offset))

    @access.user(scope=TokenScope.DATA_WRITE)
    @filtermodel(Folder)
    @autoDescribeRoute(
        Description('Create a new study.')
        .param('patientId', 'The anonymized patient identifier or MRN.')
        .param('date', 'Study date.', dataType='dateTime')
        .param('modality', 'Study modality.')
        .param('description', 'Study description.')
        .param('public', 'Public access flag', dataType='boolean', default=False)
    )
    def createStudy(self, patientId, date, modality, description, public):
        user = self.getCurrentUser()
        study = Folder().createFolder(
            parent=user, name=patientId, description=description, parentType='user', public=public,
            creator=user, allowRename=True)
        study['isStudy'] = True
        study['nSeries'] = 0
        study['studyDate'] = date
        study['patientId'] = patientId
        study['studyModality'] = modality
        return Folder().save(study)


class Series(Resource):
    def __init__(self):
        super(Series, self).__init__()
        self.resourceName = 'series'

        self.route('GET', (), self.listSeries)
        self.route('POST', (), self.createSeries)

    @access.public(scope=TokenScope.DATA_READ)
    @filtermodel(Item)
    @autoDescribeRoute(
        Description('List series in a study.')
        .modelParam('studyId', 'The ID of the parent study.', paramType='query', model=Folder,
                    level=AccessType.READ)
        .pagingParams(defaultSort='name', defaultLimit=500)
    )
    def listSeries(self, folder, limit, offset, sort):
        return list(Folder().childItems(folder, limit=limit, offset=offset, sort=sort, filters={
            'isSeries': True
        }))

    @access.user(scope=TokenScope.DATA_WRITE)
    @filtermodel(Item)
    @autoDescribeRoute(
        Description('Create a new series.')
        .modelParam('studyId', 'The parent study.', model=Folder, level=AccessType.WRITE,
                    paramType='query')
        .param('name', 'The name of the series.')
    )
    def createSeries(self, folder, name):
        series = Item().createItem(name, creator=self.getCurrentUser(), folder=folder)
        series['isSeries'] = True
        series = Item().save(series)

        Folder().update({
            '_id': folder['_id']
        }, {
            '$inc': {'nSeries': 1}
        }, multi=False)

        return series


def _decrementSeriesCount(event):
    item = event.info
    if item.get('isSeries') is True:
        Folder().update({
            '_id': item['folderId']
        }, {
            '$inc': {'nSeries': -1}
        }, multi=False)


@setting_utilities.validator(PluginSettings.STUDIES_COLL_ID)
def _validateStudiesColl(doc):
    Collection().load(doc['value'], exc=True, force=True)


def _authenticateGuestUser(event):
    # Guest login skips password validation since it's open to anyone
    if event.info['login'] == 'guest':
        guest = User().findOne({'login': 'guest'})
        event.addResponse(guest).preventDefault().stopPropagation()


@access.cookie
@access.public(scope=TokenScope.DATA_READ)
@autoDescribeRoute(
    Description('Download a DICOM thumbnail image for a given item.')
    .modelParam('id', model=Item, level=AccessType.READ)
    .param('uid', 'The UID (path) of the thumbnail file to retrieve.', paramType='path')
)
def _getThumbnail(item, uid):
    file = File().findOne({
        'attachedToId': item['_id'],
        'interactive_thumbnails_uid': uid
    })
    if not file:
        raise RestException('No such thumbnail for uid "%s".' % uid)

    return File().download(file)


@access.user(scope=TokenScope.DATA_WRITE)
@filtermodel(Job)
@autoDescribeRoute(
    Description('Generate a new set of interactive thumbnail images for a DICOM item.')
    .modelParam('id', model=Item, level=AccessType.WRITE)
)
def _createThumbnail(item):
    # Remove previously attached thumbnails
    _removeThumbnails(item, saveItem=True)

    outdir = VolumePath('__thumbnails_output__')
    return docker_run.delay(
        'girder/dicom_thumbnailer:latest', container_args=[
            '--slices', str(_THUMB_SLICES),
            '--width', str(THUMB_WIDTH),
            '--height', str(THUMB_HEIGHT),
            GirderItemIdToVolume(item['_id'], item_name=item['name']),
            outdir
        ], girder_job_title='DICOM thumbnail generation: %s' % item['name'],
        girder_result_hooks=[
            GirderUploadVolumePathToItem(outdir, item['_id'], upload_kwargs={
                'reference': json.dumps({'interactive_thumbnail': True})
            })
        ]).job


class StrokePlugin(GirderPlugin):
    DISPLAY_NAME = 'Stroke assessment application'

    def load(self, info):
        getPlugin('worker').load(info)

        dist = os.path.join(os.path.dirname(__file__), 'dist')
        webroot = staticFile(os.path.join(dist, 'index.html'))
        registerPluginWebroot(webroot, 'stroke')

        info['config']['/stroke_static'] = {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.join(dist, 'stroke_static')
        }

        info['config']['/itk'] = {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.join(dist, 'itk')
        }

        info['apiRoot'].study = Study()
        info['apiRoot'].series = Series()
        info['apiRoot'].item.route('GET', (':id', 'dicom_thumbnail', ':uid'), _getThumbnail)
        info['apiRoot'].item.route('POST', (':id', 'dicom_thumbnail'), _createThumbnail)

        Folder().ensureIndex(('isStudy', {'sparse': True}))
        Folder().exposeFields(level=AccessType.READ, fields={
            'isStudy', 'nSeries', 'studyDate', 'patientId', 'studyModality'})
        Item().exposeFields(level=AccessType.READ, fields={'isSeries'})

        events.bind('model.file.finalizeUpload.after', 'stroke', _handleUpload)
        events.bind('model.item.remove', 'stroke.decrement_series_count', _decrementSeriesCount)
        events.bind(
            'model.item.remove', 'stroke.clean_thumbnails', lambda e: _removeThumbnails(e.info))

        # Guest user support
        events.bind('model.user.authenticate', 'stroke', _authenticateGuestUser)
        try:
            User().createUser(
                login='guest', password='guestpass', firstName='Guest', lastName='User',
                email='guest@algorithms.kitware.com')
        except ValidationException:
            pass
