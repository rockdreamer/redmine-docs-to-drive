import os
import random
import datetime
from hashlib import md5

import redis
import simplejson
import celery
import magic
from googleapiclient.http import MediaFileUpload
from celery.utils.log import get_task_logger
from celery import Celery, current_task
from sqlalchemy.exc import IntegrityError
from sqlalchemy import exists, and_
from apiclient import errors

import celeryconfig
from model import *
from db import db_session
from google_api import drive_service

__author__ = 'rdfm'

logger = get_task_logger(__name__)

app = Celery()
app.config_from_object('celeryconfig')
random.seed()

REDIS_CLIENT = redis.Redis()


def get_basedir():
    basedir = db_session.query(RedmineBasedirToDriveMapping).filter_by(redmine_id=0).first()

    if basedir and basedir.drive_id:
        return basedir.drive_id

    return None


class RedmineMigrationTask(celery.Task):
    """An abstract Celery Task that ensures that the connection the the
    database is closed on task completion"""
    abstract = True
    lock_expire = 5  # 5 minutes

    def __init__(self, *a, **kw):
        super(RedmineMigrationTask, self).__init__(*a, **kw)
        self.__lock_key = None
        self.__lock = None
        self.__locked = False

    def lock_key(self, *a, **kw):
        if not a:
            a = self.request.args
        if not kw:
            kw = self.request.kwargs
        s = simplejson.dumps({'a': a, 'kw': kw})
        h = md5(s).hexdigest()
        return "%s-lock-%s" % (self.name, h)

    def try_acquire_lock(self, *a, **kw):
        """
        Check if is already locked, if not, lock it
        """
        self.__lock_key = self.lock_key(*a, **kw)
        self.__lock = REDIS_CLIENT.lock(self.__lock_key, timeout=self.lock_expire)
        self.__locked = self.__lock.acquire(blocking=False)
        if self.__locked:
            logger.debug("Lock created for %s." % self.name)

        return self.__locked

    def release_lock(self):
        # memcache delete is very slow, but we have to use it to take
        # advantage of using add() for atomic locking
        if not self.__locked:
            return
        self.__lock.release()
        logger.debug("Released lock for %s with key %s" % (
            self.name, self.__lock_key))

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        logger.debug("Removing db_session reference and task lock for %s" % self.name)
        db_session.remove()
        self.release_lock()


def get_projects_with_dmsf_revisions():
    projects = db_session.query(Project).filter(
        exists().where(and_(Project.id == DmsfFileRevision.project_id, DmsfFileRevision.deleted == 0))).all()
    return projects


def get_projects_with_documents():
    projects = db_session.query(Project).filter(exists().where(Project.id == Document.project_id)).all()
    return projects


@app.task(base=RedmineMigrationTask)
def update_project_tree_structure():
    for revision in db_session.query(DmsfFileRevision).filter_by(deleted=0):
        create_dmsf_revision_on_drive.delay(revision.id, revision.name)
    for attachment in db_session.query(DocumentAttachment):
        create_document_attachment_on_drive.delay(attachment.id, attachment.filename)


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_dmsf_revision_on_drive(self, revision_redmine_id, attachment_name):
    if not revision_redmine_id:
        raise Exception("revision_redmine_id is required")
    if not attachment_name:
        raise Exception("attachment_name is required")

    if not self.try_acquire_lock():
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    revision = db_session.query(DmsfFileRevision).filter_by(id=revision_redmine_id).first()
    if not revision:
        logger.error("No dmsf revision with id %s", revision_redmine_id)
        raise "Bad dmsf revision id passed" % revision_redmine_id

    folder = revision.folder
    print "revision folder %s" % revision.folder
    if not folder:
        folder = revision.dmsf_file.folder
    print " file folder %s" % revision.dmsf_file.folder

    if not folder:
        # place on root DMSF
        if len(revision.project.drive_dmsf) == 0 or not revision.project.drive_dmsf[0].drive_id:
            logger.info("Project DMSF Folder %s has no drive mapping, calling creation, will retry",
                        revision.project.name)
            create_project_dmsf_folder_on_drive.delay(project_redmine_id=revision.project.id,
                                                      project_name=revision.project.name)
            self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))
        parent_drive_id = revision.project.drive_dmsf[0].drive_id
    else:

        if len(folder.drive) == 0 or not folder.drive[0].drive_id:
            logger.info("DMSF Folder %s has no drive mapping, calling creation, will retry",
                        revision.dmsf_file.folder.title)
            create_dmsf_folder_on_drive.delay(folder_redmine_id=revision.dmsf_file.folder.id,
                                              folder_name=revision.dmsf_file.folder.title)
            self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))
        parent_drive_id = folder.drive[0].drive_id

    local_path = os.path.join(celeryconfig.REDMINE_TO_DRIVE_DMSF_FOLDER,
                              "p_%s" % revision.project.identifier,
                              revision.disk_filename)
    if not os.path.isfile(local_path):
        local_path = os.path.join(celeryconfig.REDMINE_TO_DRIVE_DMSF_FOLDER,
                                  revision.disk_filename)
        if not os.path.isfile(local_path):
            logger.error("File missing %s", local_path)

    filename, file_extension = os.path.splitext(revision.name)

    remote_name = "%s (redmine version %d.%d)%s" % (
        filename, revision.major_version, revision.minor_version, file_extension)
    version = (revision.major_version * 10000) + revision.minor_version
    description = "Created from DMSF revision id %s\nTitle: %s\nComment: %s\nDescription: %s" % \
                  (revision.id, revision.title, revision.comment, revision.description)
    return create_single_version_file_on_drive(self,
                                               parent_drive_id=parent_drive_id,
                                               redmine_type="dmsf_file_revision",
                                               redmine_id=revision_redmine_id,
                                               file_name=remote_name,
                                               local_path=local_path,
                                               description=description,
                                               mime_type=revision.mime_type,
                                               version=version,
                                               modified_date=revision.updated_at)


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_document_attachment_on_drive(self, attachment_redmine_id, attachment_name):
    if not attachment_redmine_id:
        raise Exception("attachment_redmine_id is required")
    if not attachment_name:
        raise Exception("attachment_name is required")

    if not self.try_acquire_lock():
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    attachment = db_session.query(DocumentAttachment).filter_by(id=attachment_redmine_id).first()
    if not attachment:
        logger.error("No document attachment with id %s", attachment_redmine_id)
        raise "Bad attachment id passed" % attachment_redmine_id

    if len(attachment.document.drive) == 0 or not attachment.document.drive[0].drive_id:
        logger.info("Document %s has no drive mapping, calling creation, will retry", attachment.document.title)
        create_document_folder_on_drive.delay(document_redmine_id=attachment.document.id,
                                              document_name=attachment.document.title)
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    if attachment.disk_directory:
        local_path = os.path.join(celeryconfig.REDMINE_TO_DRIVE_FILES_FOLDER, attachment.disk_directory,
                                  attachment.disk_filename)
    else:
        local_path = os.path.join(celeryconfig.REDMINE_TO_DRIVE_FILES_FOLDER, attachment.disk_filename)
    return create_single_version_file_on_drive(self,
                                               parent_drive_id=attachment.document.drive[0].drive_id,
                                               redmine_type="document_attachment",
                                               redmine_id=attachment_redmine_id,
                                               file_name=attachment.filename,
                                               local_path=local_path,
                                               description=attachment.description,
                                               mime_type=attachment.content_type,
                                               version=1,
                                               modified_date=attachment.created_on)


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_document_folder_on_drive(self, document_redmine_id, document_name):
    if not document_redmine_id:
        raise Exception("document_redmine_id is required")
    if not document_name:
        raise Exception("document_name is required")

    if not self.try_acquire_lock():
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    document = db_session.query(Document).filter_by(id=document_redmine_id).first()
    if not document:
        logger.error("No document with id %s", document_redmine_id)
        raise "Bad document id passed" % document_redmine_id

    if len(document.project.drive_documents) == 0 or not document.project.drive_documents[0].drive_id:
        logger.info("Project %s has no drive documents mapping, calling creation, will retry", document.project.name)
        create_project_documents_folder_on_drive.delay(project_redmine_id=document.project.id,
                                                       project_name=document.project.name)
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    return create_folder_on_drive(self, document.project.drive_documents[0].drive_id, 'document',
                                  document_redmine_id, document.title)


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_dmsf_folder_on_drive(self, folder_redmine_id, folder_name):
    if not folder_redmine_id:
        raise Exception("folder_redmine_id is required")
    if not folder_name:
        raise Exception("folder_name is required")

    if not self.try_acquire_lock():
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    folder = db_session.query(DmsfFolder).filter_by(id=folder_redmine_id).first()
    if not folder:
        logger.error("No DMSF Folder with id %s", folder_redmine_id)
        raise "Bad DMSF id passed" % folder_redmine_id

    if folder.parent:
        if len(folder.parent.drive) == 0 or not folder.parent.drive[0].drive_id:
            logger.info("Parent DMSF Folder %s of %s has no drive mapping, calling creation, will retry",
                        folder.parent.title, folder.title)
            create_dmsf_folder_on_drive.delay(folder_redmine_id=folder.parent.id, folder_name=folder.parent.title)
            self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))
        return create_folder_on_drive(self, folder.parent.drive[0].drive_id, 'dmsf_folder',
                                      folder_redmine_id, folder.title)

    else:
        if len(folder.project.drive_dmsf) == 0 or not folder.project.drive_dmsf[0].drive_id:
            logger.info("Project DMSF Folder %s has no drive mapping, calling creation, will retry",
                        folder.project.name)
            create_project_dmsf_folder_on_drive.delay(project_redmine_id=folder.project.id,
                                                      project_name=folder.project.name)
            self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))
        return create_folder_on_drive(self, folder.project.drive_dmsf[0].drive_id, 'dmsf_folder',
                                      folder_redmine_id, folder.title)


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_dmsf_folder_on_drive(self, folder_redmine_id, folder_name):
    if not folder_redmine_id:
        raise Exception("folder_redmine_id is required")
    if not folder_name:
        raise Exception("folder_name is required")

    if not self.try_acquire_lock():
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    folder = db_session.query(DmsfFolder).filter_by(id=folder_redmine_id).first()
    if not folder:
        logger.error("No DMSF Folder with id %s", folder_redmine_id)
        raise "Bad DMSF id passed" % folder_redmine_id

    if folder.parent:
        if len(folder.parent.drive) == 0 or not folder.parent.drive[0].drive_id:
            logger.info("Parent DMSF Folder %s of %s has no drive mapping, calling creation, will retry",
                        folder.parent.title, folder.title)
            create_dmsf_folder_on_drive.delay(folder_redmine_id=folder.parent.id, folder_name=folder.parent.title)
            self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))
        return create_folder_on_drive(self, folder.parent.drive[0].drive_id, 'dmsf_folder',
                                      folder_redmine_id, folder.title)

    else:
        if len(folder.project.drive_dmsf) == 0 or not folder.project.drive_dmsf[0].drive_id:
            logger.info("Project DMSF Folder %s has no drive mapping, calling creation, will retry",
                        folder.project.name)
            create_project_dmsf_folder_on_drive.delay(project_redmine_id=folder.project.id,
                                                      project_name=folder.project.name)
            self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))
        return create_folder_on_drive(self, folder.project.drive_dmsf[0].drive_id, 'dmsf_folder',
                                      folder_redmine_id, folder.title)


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_project_dmsf_folder_on_drive(self, project_redmine_id, project_name):
    if not project_redmine_id:
        raise Exception("project_redmine_id is required")
    if not project_name:
        raise Exception("folder_name is required")

    if not self.try_acquire_lock():
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    project = db_session.query(Project).filter_by(id=project_redmine_id).first()
    if not project:
        logger.error("No project with id %s", project_redmine_id)
        raise "Bad project id passed" % project_redmine_id

    if len(project.drive_project) == 0 or not project.drive_project[0].drive_id:
        logger.info("Project %s has no drive mapping, calling creation, will retry", project.name)
        create_project_folder_on_drive.delay(project_redmine_id=project.id, project_name=project.name)
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    return create_folder_on_drive(self, project.drive_project[0].drive_id, 'project_dmsf',
                                  project_redmine_id, "DMSF Folders")


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_project_documents_folder_on_drive(self, project_redmine_id, project_name):
    if not project_redmine_id:
        raise Exception("project_redmine_id is required")
    if not project_name:
        raise Exception("folder_name is required")

    if not self.try_acquire_lock():
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    project = db_session.query(Project).filter_by(id=project_redmine_id).first()
    if not project:
        logger.error("No project with id %s", project_redmine_id)
        raise "Bad project id passed" % project_redmine_id

    if len(project.drive_project) == 0 or not project.drive_project[0].drive_id:
        logger.info("Project %s has no drive mapping, calling creation, will retry", project.name)
        create_project_folder_on_drive.delay(project_redmine_id=project.id, project_name=project.name)
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    return create_folder_on_drive(self, project.drive_project[0].drive_id, 'project_docs',
                                  project_redmine_id, "Documents")


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_project_folder_on_drive(self, project_redmine_id, project_name):
    if not project_redmine_id:
        raise Exception("project_redmine_id is required")
    if not project_name:
        raise Exception("folder_name is required")

    if not self.try_acquire_lock():
        self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    project = db_session.query(Project).filter_by(id=project_redmine_id).first()
    if not project:
        logger.error("No project with id %s", project_redmine_id)
        raise "Bad project id passed" % project_redmine_id

    if project.parent:
        if len(project.parent.drive_project) == 0 or not project.parent.drive_project[0].drive_id:
            logger.info("Parent Project %s of %s has no drive mapping, calling creation, will retry",
                        project.parent.name, project.name)
            create_project_folder_on_drive.delay(project_redmine_id=project.parent_id, project_name=project.parent.name)
            self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))
        return create_folder_on_drive(self, project.parent.drive_project[0].drive_id, 'project',
                                      project_redmine_id, project.name)

    else:
        basedir_id = get_basedir()
        if not basedir_id:
            logger.info("Project %s has no parent and basedir is missing, calling creation, will retry", project.name)
            create_basedir.delay()
            self.retry(countdown=min(2 + (2 * current_task.request.retries), 128))
        return create_folder_on_drive(self, basedir_id, 'project',
                                      project_redmine_id, project.name)


@app.task(bind=True, base=RedmineMigrationTask, max_retries=10, rate_limit=None)
def create_basedir(self):
    basedir = db_session.query(RedmineBasedirToDriveMapping).filter_by(redmine_id=0).first()
    if basedir and basedir.drive_id:
        return basedir.drive_id

    return create_folder_on_drive(self, 'root', 'basedir', 0, celeryconfig.REDMINE_TO_DRIVE_BASE_DIR)


def create_folder_on_drive(task, parent_drive_id, redmine_type, redmine_id, folder_name):
    if not parent_drive_id:
        raise Exception("parent_drive_id is required")
    if not redmine_type:
        raise Exception("redmine_type is required")
    if redmine_id is None:
        raise Exception("redmine_id is required")
    if not folder_name:
        raise Exception("folder_name is required")

    db_mapping = db_session.query(RedmineToDriveMapping).filter_by(redmine_id=redmine_id).filter_by(
        mapping_type=redmine_type).first()

    if db_mapping and db_mapping.drive_id:
        logger.info("Folder %s already mapped to %s", folder_name, db_mapping.drive_id)
        return

    if not db_mapping:
        try:
            db_mapping = RedmineToDriveMapping(redmine_id=redmine_id, mapping_type=redmine_type,
                                               last_update=datetime.datetime.utcnow())
            db_session.add(db_mapping)
            db_session.commit()
            logger.info("Created mapping for %s %s id:%s", redmine_type, folder_name, redmine_id)
        except IntegrityError, e:
            logger.info("Cannot create mapping due to duplicate, will retry: %s", e)
            db_session.rollback()
            task.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    page_token = None
    while True:
        try:
            param = {
                'q': "title='%s'" % folder_name,
            }
            if page_token:
                param['pageToken'] = page_token
            children = drive_service.children().list(folderId=parent_drive_id, **param).execute()

            for child in children.get('items', []):
                redmine_id = child['id']
                logger.info("Found remote folder %s with id %s, adding to db", folder_name, redmine_id)
                db_mapping.drive_id = redmine_id
                db_mapping.last_update = datetime.datetime.utcnow()
                db_session.commit()
                return redmine_id

            page_token = children.get('nextPageToken')
            if not page_token:
                logger.info("Creating drive folder for %s %s id:%s", redmine_type, folder_name, redmine_id)
                # Create a folder on Drive, returns the newely created folders ID
                body = {
                    'title': folder_name,
                    'mimeType': "application/vnd.google-apps.folder",
                    'parents': [{'id': parent_drive_id}]
                }
                m_folder = drive_service.files().insert(body=body).execute()
                db_mapping.drive_id = m_folder['id']
                db_session.commit()
                logger.info("Created drive folder for %s %s id:%s", redmine_type, folder_name, redmine_id)
                return db_mapping.drive_id
        except errors.HttpError, error:
            logger.info("Cannot create drive folder for %s %s id:%s: %s", redmine_type, folder_name, redmine_id,
                        error)


def create_single_version_file_on_drive(task, parent_drive_id, redmine_type, redmine_id,
                                        file_name, local_path, description, mime_type,
                                        version, modified_date):
    if not parent_drive_id:
        raise Exception("parent_drive_id is required")
    if not redmine_type:
        raise Exception("redmine_type is required")
    if redmine_id is None:
        raise Exception("redmine_id is required")
    if not file_name:
        raise Exception("folder_name is required")
    if not local_path:
        raise Exception("local_path is required")
    if not os.path.isfile(local_path):
        raise Exception("local_path %s is missing" % local_path)

    db_mapping = db_session.query(RedmineToDriveMapping).filter_by(redmine_id=redmine_id).filter_by(
        mapping_type=redmine_type).first()

    if db_mapping and db_mapping.drive_id:
        logger.info("File %s already mapped to %s", file_name, db_mapping.drive_id)
        return

    if not db_mapping:
        try:
            db_mapping = RedmineToDriveMapping(redmine_id=redmine_id, mapping_type=redmine_type,
                                               last_update=datetime.datetime.utcnow())
            db_session.add(db_mapping)
            db_session.commit()
            logger.info("Created mapping for %s %s id:%s", redmine_type, file_name, redmine_id)
        except IntegrityError, e:
            logger.info("Cannot create mapping due to duplicate, will retry: %s", e)
            db_session.rollback()
            task.retry(countdown=min(2 + (2 * current_task.request.retries), 128))

    page_token = None
    while True:
        try:
            param = {
                'q': "title='%s'" % file_name,
            }
            if page_token:
                param['pageToken'] = page_token
            children = drive_service.children().list(folderId=parent_drive_id, **param).execute()

            for child in children.get('items', []):
                redmine_id = child['id']
                logger.info("Found remote file %s with id %s, adding to db", file_name, redmine_id)
                db_mapping.drive_id = redmine_id
                db_mapping.last_update = datetime.datetime.utcnow()
                db_session.commit()
                return redmine_id

            page_token = children.get('nextPageToken')
            if not page_token:
                logger.info("Creating file for %s %s id:%s", redmine_type, file_name, redmine_id)
                if not mime_type or mime_type == '':
                    mime_type = magic.from_file(local_path, mime=True)
                    logger.info("Replaced missing mimetype for %s to %s", file_name, mime_type)

                # Create the file on Drive
                media_body = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
                body = {
                    'title': file_name,
                    'mimeType': mime_type
                }
                if modified_date:
                    body['modifiedDate'] = modified_date.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                body['description'] = description + "\nCreated from %s id %s" % (redmine_type, redmine_id)
                body['version'] = version
                body['parents'] = [{'id': parent_drive_id}]

                m_file = drive_service.files().insert(body=body, media_body=media_body,
                                                      useContentAsIndexableText=True,
                                                      pinned=True).execute()
                db_mapping.drive_id = m_file['id']
                db_session.commit()
                logger.info("Created file for %s %s id:%s", redmine_type, file_name, redmine_id)
                return db_mapping.drive_id
        except errors.HttpError, error:
            logger.info("Cannot create file for %s %s id:%s: %s", redmine_type, file_name, redmine_id,
                        error)
