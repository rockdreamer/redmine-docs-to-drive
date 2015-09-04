__author__ = 'rdfm'

import argparse
import sys
import os

import httplib2
from apiclient import discovery, errors
from oauth2client import client
from oauth2client.file import Storage
from oauth2client.tools import run
from googleapiclient.http import MediaFileUpload
import MySQLdb as mdb
import magic


class RedmineProjectCollection:
    def __init__(self, remote_basedir, connection, drive_service, dmsf_local_folder, documents_local_folder):
        self.projectsMap = {}
        self.rootProjects = []
        self.remote_basedir = remote_basedir
        self.drive_service = drive_service
        self.db_connection = connection
        self.dmsf_local_folder = dmsf_local_folder
        self.documents_local_folder = documents_local_folder
        self.remote_basedir_id = None

    def load_from_db(self):
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("""select id,name,description,homepage,parent_id,identifier,status from projects""")
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        for row in rows:
            project = RedmineProject(self.drive_service, self.db_connection, self.dmsf_local_folder,
                                     self.documents_local_folder)
            project.id = row[0]
            project.name = row[1]
            project.description = row[2]
            project.homepage = row[3]
            project.parent_id = row[4]
            project.identifier = row[5]
            self.projectsMap[project.id] = project
            print "Loaded from db Project id:%s name:%s" % (project.id, project.name)

        try:
            cursor.execute("""select id ,drive_id from redmine_to_drive where type='project'""")
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        for row in rows:
            self.projectsMap[row[0]].drive_id = row[1]
            print "Loaded Mapping Project id:%s name:%s -> %s" % (row[0], self.projectsMap[row[0]].name, row[1])

        try:
            cursor.execute("""select id ,drive_id from redmine_to_drive where type='project_dmsf'""")
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        for row in rows:
            self.projectsMap[row[0]].drive_dmsf_id = row[1]
            print "Loaded Mapping DMSF id:%s name:%s -> %s" % (row[0], self.projectsMap[row[0]].name, row[1])

        try:
            cursor.execute("""select id ,drive_id from redmine_to_drive where type='project_docs'""")
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        for row in rows:
            self.projectsMap[row[0]].drive_documents_id = row[1]
            print "Loaded Mapping Documents id:%s name:%s -> %s" % (row[0], self.projectsMap[row[0]].name, row[1])

        try:
            cursor.execute("""select drive_id from redmine_to_drive where type='basedir'""")
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        sql_remote_basedir_id = None
        for row in rows:
            print "Loaded Mapping BaseDir -> %s" % (row[0])
            sql_remote_basedir_id = row[0]

        if sql_remote_basedir_id and sql_remote_basedir_id != self.remote_basedir_id:
            print "Error, remote_basedir_id mismatch"
            sys.exit(1)

        for project in self.projectsMap.itervalues():
            if project.parent_id:
                self.projectsMap[project.parent_id].children.append(project)
                project.parent = self.projectsMap[project.parent_id]

        for project in sorted(self.projectsMap.values(), key=RedmineProject.path):
            project.load_users_from_db()
            project.load_documents_from_db()
            project.load_dmsf_folders_from_db()

        self.rootProjects = [i for i in sorted(self.projectsMap.values(), key=RedmineProject.path) if i.is_root()]

        print "loaded %d root of %d projects" % (len(self.rootProjects), len(self.projectsMap.values()))

    def lookup_remote_basedir_id(self):
        if self.remote_basedir_id:
            return

        page_token = None
        while True:
            try:
                param = {}
                if page_token:
                    param['pageToken'] = page_token
                children = self.drive_service.children().list(folderId='root',
                                                              q="title='%s'" % self.remote_basedir).execute()

                for child in children.get('items', []):
                    print 'Basedir Id: %s' % child['id']
                    self.remote_basedir_id = child['id']
                    return

                page_token = children.get('nextPageToken')
                if not page_token:
                    print "Cannot find %s in drive" % self.remote_basedir
                    return
            except errors.HttpError, error:
                print 'An error occurred: %s' % error
                sys.exit(1)

    def create_remote_project_folders(self):
        if not self.remote_basedir_id:
            # Create remote basedir folder on Drive
            body = {
                'title': self.remote_basedir,
                'mimeType': "application/vnd.google-apps.folder"
            }
            body['parents'] = [{'id': 'root'}]

            m_folder = self.drive_service.files().insert(body=body).execute()
            self.remote_basedir_id = m_folder['id']
            try:
                cur = self.db_connection.cursor()
                cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""", (0, "basedir", self.remote_basedir_id))
                self.db_connection.commit()
            except mdb.Error, e:
                print "Error %d: %s" % (e.args[0], e.args[1])
                sys.exit(1)

            print 'Created basedir folder on Drive: id:%s' % self.remote_basedir_id

        for project in self.rootProjects:
            project.create_remote_if_missing(self.remote_basedir_id)


class RedmineProject:
    """Representation of redmine project"""

    def __init__(self, drive_service, connection, dmsf_local_folder, documents_local_folder):
        self.id = 0
        self.drive_service = drive_service
        self.db_connection = connection
        self.dmsf_local_folder = dmsf_local_folder
        self.documents_local_folder = documents_local_folder
        self.name = ''
        self.owners = set()
        self.description = ''
        self.homepage = ''
        self.parent_id = 0
        self.identifier = 0
        self.parent = None
        self.drive_id = None
        self.drive_dmsf_id = None
        self.drive_documents_id = None
        self.children = []
        self.documents = []
        self.dmsfFoldersMap = {}
        self.dmsfRootFolders = []

    def path(self):
        if self.parent:
            return self.parent.name + '/' + self.name
        else:
            return self.name

    def is_root(self):
        return not self.parent

    def load_users_from_db(self):
        try:
            cursor = self.db_connection.cursor()
            cursor.execute(
                "select distinct(u.mail) from members m, users u WHERE m.user_id = u.id and m.project_id = %s",
                [self.id])
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        if self.parent:
            self.owners = self.parent.owners.copy()

        for row in rows:
            if row[0] and row[0] != '':
                self.owners.add(row[0])

    def load_dmsf_folders_from_db(self):
        try:
            cursor = self.db_connection.cursor()
            cursor.execute(
                """select id,dmsf_folder_id,title,description from dmsf_folders where project_id='%d'""" % (self.id,))
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        for row in rows:
            folder = DmsfFolder(drive_service=self.drive_service, connection=self.db_connection, project=self)
            folder.id = row[0]
            folder.parent_id = row[1]
            folder.name = row[2]
            folder.description = row[3]
            self.dmsfFoldersMap[folder.id] = folder
            try:
                cursor.execute("""select drive_id from redmine_to_drive where type='dmsf_folder' and id=%s""",
                               (folder.id,))
                rdrows = cursor.fetchall()
            except mdb.Error, e:
                print "Error %d: %s" % (e.args[0], e.args[1])
                sys.exit(1)

            for rdrow in rdrows:
                folder.drive_id = rdrow[0]

            print "Loaded DMSF Folder from db project:%s id:%s name:%s -> %s" % (
            self.id, folder.id, folder.name, (folder.drive_id,))
            folder.load_dmsf_files_from_db()

        for folder in self.dmsfFoldersMap.itervalues():
            if folder.parent_id:
                self.dmsfFoldersMap[folder.parent_id].children.append(folder)
                folder.parent = self.dmsfFoldersMap[folder.parent_id]
            else:
                self.dmsfRootFolders.append(folder)

    def load_documents_from_db(self):
        try:
            cursor = self.db_connection.cursor()
            cursor.execute(
                """select id,title,description from documents where project_id='%d'""" % (self.id,))
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        for row in rows:
            folder = Document(drive_service=self.drive_service, connection=self.db_connection, project=self)
            folder.id = row[0]
            folder.name = row[1]
            folder.description = row[2]
            self.documents.append(folder)
            try:
                cursor.execute("""select drive_id from redmine_to_drive where type='document' and id=%s""",
                               (folder.id,))
                rdrows = cursor.fetchall()
            except mdb.Error, e:
                print "Error %d: %s" % (e.args[0], e.args[1])
                sys.exit(1)

            for rdrow in rdrows:
                folder.drive_id = rdrow[0]

            print "Loaded Document from db project:%s id:%s name:%s -> %s" % (
            self.id, folder.id, folder.name, (folder.drive_id,))
            folder.load_document_files_from_db()

    def add_to_db(self):
        try:
            cur = self.db_connection.cursor()
            cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""", (self.id, "project", self.drive_id))
            self.db_connection.commit()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

    def add_dmsf_root_to_db(self):
        try:
            cur = self.db_connection.cursor()
            cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""",
                        (self.id, "project_dmsf", self.drive_dmsf_id))
            self.db_connection.commit()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

    def add_documents_root_to_db(self):
        try:
            cur = self.db_connection.cursor()
            cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""",
                        (self.id, "project_docs", self.drive_documents_id))
            self.db_connection.commit()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

    def must_be_created(self):
        if len(self.dmsfRootFolders) > 0:
            return True
        for document in self.documents:
            if len(document.children) > 0:
                return True
        for child in self.children:
            if child.must_be_created():
                return True
        return False

    def create_remote_if_missing(self, base_id):
        if not self.must_be_created():
            return

        if not self.drive_id:
            # Create a folder on Drive, returns the newely created folders ID
            body = {
                'title': self.name,
                'mimeType': "application/vnd.google-apps.folder"
            }
            if self.parent:
                body['parents'] = [{'id': self.parent.drive_id}]
            else:
                body['parents'] = [{'id': base_id}]
            m_folder = self.drive_service.files().insert(body=body).execute()
            self.drive_id = m_folder['id']
            self.add_to_db()
            print 'Created Project folder on Drive: %s -> %s' % (self.name, self.drive_id)

        if len(self.documents) > 0:
            body = {
                'title': "documents",
                'mimeType': "application/vnd.google-apps.folder"
            }
            body['parents'] = [{'id': self.drive_id}]
            m_folder = self.drive_service.files().insert(body=body).execute()
            self.drive_documents_id = m_folder['id']
            self.add_documents_root_to_db()
            print 'Created Project documents folder on Drive: %s -> %s' % (self.name, self.drive_id)

        if len(self.dmsfRootFolders) > 0:
            body = {
                'title': "dmsf",
                'mimeType': "application/vnd.google-apps.folder"
            }
            body['parents'] = [{'id': self.drive_id}]
            m_folder = self.drive_service.files().insert(body=body).execute()
            self.drive_dmsf_id = m_folder['id']
            self.add_dmsf_root_to_db()
            print 'Created Project dmsf folder on Drive: %s -> %s' % (self.name, self.drive_id)


        for document in self.documents:
            document.create_remote_if_missing()

        for dmsf_folder in self.dmsfRootFolders:
            dmsf_folder.create_remote_if_missing()

        for child in self.children:
            child.create_remote_if_missing(base_id)



class DmsfFolder:
    def __init__(self, project, drive_service, connection):
        self.project = project
        self.id = 0
        self.drive_service = drive_service
        self.db_connection = connection
        self.name = ''
        self.description = ''
        self.parent = None
        self.children = []
        self.drive_id = None

    def load_dmsf_files_from_db(self):
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("""select
                        id, dmsf_file_id, name, disk_filename, size, mime_type, title,
                        description, major_version, minor_version, comment
                        from dmsf_file_revisions where deleted=0 and dmsf_folder_id='%d' ORDER BY dmsf_file_id, major_version, minor_version""" % (
            self.id,))
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        file = None
        for row in rows:
            if not file or file.id != row[1]:
                file = DmsfFile(project=self.project, drive_service=self.drive_service, connection=self.db_connection,
                                folder=self)
                file.id = row[1]
                file.name = row[2]
                try:
                    cursor.execute("""select drive_id from redmine_to_drive where type='dmsf_file' and id=%s""",
                                   (file.id,))
                    rdrows = cursor.fetchall()
                except mdb.Error, e:
                    print "Error %d: %s" % (e.args[0], e.args[1])
                    sys.exit(1)

                for rdrow in rdrows:
                    file.drive_id = rdrow[0]
                self.children.append(file)
                print "Loaded DMSF File from db project:%s id:%s name:%s -> %s" % (
                self.project.id, file.id, file.name, (file.drive_id,))

            revision = DmsfFileRevision(drive_service=self.drive_service, connection=self.db_connection, file=file)
            revision.id = row[0]
            revision.name = row[2]
            revision.disk_filename = os.path.join(self.project.dmsf_local_folder, "p_%s" % self.project.identifier,
                                                  row[3])
            if not os.path.isfile(revision.disk_filename):
                print "File missing %s" % revision.disk_filename
                revision.disk_filename = os.path.join(self.project.dmsf_local_folder, row[3])
                if not os.path.isfile(revision.disk_filename):
                    print "File missing %s" % revision.disk_filename
                    revision.disk_filename = None
                else:
                    print "File found in %s" % revision.disk_filename

            revision.size = row[4]
            revision.mime_type = row[5]
            if not revision.mime_type and revision.disk_filename:
                revision.mime_type = magic.from_file(revision.disk_filename, mime=True)
                print "Replaced missing mimetype for %s to %s" % (revision.disk_filename, revision.mime_type)
            revision.title = row[6]
            revision.description = row[7]
            revision.major_version = row[8]
            revision.minor_version = row[9]
            revision.comment = row[10]
            try:
                cursor.execute("""select drive_id from redmine_to_drive where type='dmsf_file_revision' and id=%s""",
                               (revision.id,))
                revrows = cursor.fetchall()
            except mdb.Error, e:
                print "Error %d: %s" % (e.args[0], e.args[1])
                sys.exit(1)

            for revrow in revrows:
                revision.drive_id = revrow[0]
            if revision.disk_filename:
                file.revisions.append(revision)
                print "Loaded DMSF File Revision from db project:%s id:%s name:%s v%d.%d -> %s" % (
                self.project.id, revision.id, revision.name, revision.major_version, revision.minor_version,
                (revision.drive_id,))
            else:
                print "Loaded Skipped DMSF File Revision (file missing) from db project:%s id:%s name:%s v%d.%d -> %s" % (
                self.project.id, revision.id, revision.name, revision.major_version, revision.minor_version,
                (revision.drive_id,))

    def add_to_db(self):
        try:
            cur = self.db_connection.cursor()
            cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""", (self.id, "dmsf_folder", self.drive_id))
            self.db_connection.commit()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

    def create_remote_if_missing(self):
        if not self.drive_id:
            # Create a folder on Drive, returns the newely created folders ID
            body = {
                'title': self.name,
                'mimeType': "application/vnd.google-apps.folder"
            }
            if self.parent:
                body['parents'] = [{'id': self.parent.drive_id}]
            else:
                body['parents'] = [{'id': self.project.drive_dmsf_id}]
            m_folder = self.drive_service.files().insert(body=body).execute()
            self.drive_id = m_folder['id']

            self.add_to_db()
            print 'Created DMSF folder on Drive: project:%s id:%s name:%s -> %s' % (
            self.project.id, self.id, self.name, self.drive_id,)

        for child in self.children:
            child.create_remote_if_missing()


class DmsfFile:
    def __init__(self, project, folder, drive_service, connection):
        self.project = project
        self.parent = folder
        self.drive_service = drive_service
        self.db_connection = connection
        self.id = 0
        self.name = ''
        self.revisions = []
        self.drive_id = None

    def add_to_db(self):
        try:
            cur = self.db_connection.cursor()
            cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""", (self.id, "dmsf_file", self.drive_id))
            self.db_connection.commit()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

    def create_remote_if_missing(self):
        if len(self.revisions) == 0:
            print u'Skipping creation of DMSF file on Drive (no revisions): project:%s id:%s name:%s' % (
            self.project.id, self.id, self.name)
            return

        if not self.drive_id:
            try:
                m_ids = self.drive_service.files().generateIds(maxResults=1).execute()
            except errors.HttpError, error:
                print 'An error occured: %s' % error
                sys.exit(1)
            self.drive_id = m_ids['ids'][0];
            self.add_to_db()
            print 'Created DMSF file on Drive: project:%s id:%s name:%s -> %s' % (
            self.project.id, self.id, self.name, self.drive_id)

        file_is_uploaded = False
        for revision in self.revisions:
            if revision.drive_id:
                file_is_uploaded = True

            if not revision.drive_id:
                if file_is_uploaded:
                    # update existing id
                    media_body = MediaFileUpload(self.revisions[0].disk_filename, mimetype=self.revisions[0].mime_type,
                                                 resumable=True)
                    body = {
                        'id': self.drive_id,
                        'title': revision.name,
                        'mimeType': revision.mime_type
                    }
                    body['description'] = revision.description + "\nCreated from revision %d.%d from DMSF id %s" % (
                    revision.major_version, revision.minor_version, self.id)
                    body['version'] = (revision.major_version * 10000) + revision.minor_version
                    body['parents'] = [{'id': self.parent.drive_id}]

                    try:
                        m_file = self.drive_service.files().update(fileId=self.drive_id, body=body, newRevision=True,
                                                                   media_body=media_body,
                                                                   useContentAsIndexableText=True,
                                                                   pinned=True).execute()
                    except errors.HttpError, error:
                        print 'An error occured: %s' % error
                        sys.exit(1)

                    revision.drive_id = m_file['headRevisionId']
                else:
                    # Create the file on Drive
                    media_body = MediaFileUpload(self.revisions[0].disk_filename, mimetype=self.revisions[0].mime_type,
                                                 resumable=True)
                    body = {
                        'id': self.drive_id,
                        'title': revision.name,
                        'mimeType': revision.mime_type
                    }
                    body['description'] = revision.description + "\nCreated from revision %d.%d from DMSF id %s" % (
                    revision.major_version, revision.minor_version, self.id)
                    body['version'] = (revision.major_version * 10000) + revision.minor_version
                    body['parents'] = [{'id': self.parent.drive_id}]

                    try:
                        m_file = self.drive_service.files().insert(body=body, media_body=media_body,
                                                                   useContentAsIndexableText=True,
                                                                   pinned=True).execute()
                    except errors.HttpError, error:
                        print 'An error occured: %s' % error
                        sys.exit(1)

                    revision.drive_id = m_file['headRevisionId']
                revision.add_to_db()
                file_is_uploaded = True
                print 'Created DMSF file revision on Drive: project:%s id:%s name:%s -> %s revision %s' % (
                self.project.id, self.id, self.name, self.drive_id, revision.drive_id)


class DmsfFileRevision:
    def __init__(self, file, drive_service, connection):
        self.file = file
        self.drive_service = drive_service
        self.db_connection = connection
        self.drive_id = None
        self.id = 0
        self.name = ''
        self.disk_filename = None
        self.size = 0
        self.mime_type = ''
        self.title = ''
        self.description = ''
        self.major_version = 0
        self.minor_version = 0
        self.comment = ''

    def add_to_db(self):
        try:
            cur = self.db_connection.cursor()
            cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""",
                        (self.id, "dmsf_file_revision", self.drive_id))
            self.db_connection.commit()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

    def create_remote_if_missing(self):
        if not self.drive_id:
            # Create the file on Drive
            return


class Document:
    def __init__(self, project, drive_service, connection):
        self.project = project
        self.id = 0
        self.drive_service = drive_service
        self.db_connection = connection
        self.name = ''
        self.description = ''
        self.children = []
        self.drive_id = None

    def load_document_files_from_db(self):
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("""select
                        id, filename, disk_filename, filesize, content_type, description, disk_directory
                        from attachments where container_id=%d and container_type='Document'""" % (self.id,))
            rows = cursor.fetchall()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

        for row in rows:
            attachment = DocumentAttachment(self.project, self, self.drive_service, self.db_connection)
            attachment.id = row[0]
            attachment.filename = row[1]
            if row[6]:
                attachment.disk_filename = os.path.join(self.project.documents_local_folder, row[6], row[2])
            else:
                attachment.disk_filename = os.path.join(self.project.documents_local_folder, row[2])
            if not os.path.isfile(attachment.disk_filename):
                print "File missing %s" % attachment.disk_filename
                attachment.disk_filename = None
            attachment.mime_type = row[4]
            attachment.description = row[5]
            if not attachment.mime_type and attachment.disk_filename:
                attachment.mime_type = magic.from_file(attachment.disk_filename, mime=True)
                print "Replaced missing mimetype for %s to %s" % (attachment.disk_filename, attachment.mime_type)
            try:
                cursor.execute("""select drive_id from redmine_to_drive where type='document_attachment' and id=%s""",
                               (attachment.id,))
                revrows = cursor.fetchall()
            except mdb.Error, e:
                print "Error %d: %s" % (e.args[0], e.args[1])
                sys.exit(1)

            for revrow in revrows:
                attachment.drive_id = revrow[0]
            if attachment.disk_filename:
                self.children.append(attachment)
                print "Loaded Document attachment from db project:%s id:%s filename:%s -> %s" % (
                self.project.id, attachment.id, attachment.filename, attachment.drive_id)
            else:
                print "Loaded skipped Document attachment (file missing) from db project:%s id:%s filename:%s -> %s" % (
                self.project.id, attachment.id, attachment.filename, attachment.drive_id)

    def add_to_db(self):
        try:
            cur = self.db_connection.cursor()
            cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""", (self.id, "document", self.drive_id))
            self.db_connection.commit()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

    def create_remote_if_missing(self):
        if not self.drive_id:
            # Create a folder on Drive, returns the newely created folders ID
            body = {
                'title': self.name,
                'mimeType': "application/vnd.google-apps.folder"
            }
            body['parents'] = [{'id': self.project.drive_documents_id}]
            m_folder = self.drive_service.files().insert(body=body).execute()
            self.drive_id = m_folder['id']

            self.add_to_db()
            print 'Created Document folder on Drive: project:%s id:%s name:%s -> %s' % (
            self.project.id, self.id, self.name, self.drive_id,)

            for attachment in self.children:
                attachment.create_remote_if_missing()


class DocumentAttachment:
    def __init__(self, project, document, drive_service, connection):
        self.project = project
        self.parent = document
        self.drive_service = drive_service
        self.db_connection = connection
        self.id = 0
        self.drive_id = None
        self.filename = None
        self.disk_filename = None
        self.size = 0
        self.mime_type = ''
        self.description = ''

    def add_to_db(self):
        try:
            cur = self.db_connection.cursor()
            cur.execute("""insert into redmine_to_drive values(%s,%s,%s)""", (self.id, "document_attachment", self.drive_id))
            self.db_connection.commit()
        except mdb.Error, e:
            print "Error %d: %s" % (e.args[0], e.args[1])
            sys.exit(1)

    def create_remote_if_missing(self):
        if not self.drive_id:
            # Create the file on Drive
            media_body = MediaFileUpload(self.disk_filename, mimetype=self.mime_type, resumable=True)
            body = {
                'id': self.drive_id,
                'title': self.filename,
                'mimeType': self.mime_type
            }
            body['description'] = self.description
            body['parents'] = [{'id': self.parent.drive_id}]

            try:
                m_file = self.drive_service.files().insert(body=body, media_body=media_body,
                                                           useContentAsIndexableText=True,
                                                           pinned=True).execute()
            except errors.HttpError, error:
                print 'An error occured: %s' % error
                sys.exit(1)
            self.drive_id = m_file['id'];
            self.add_to_db()

            print 'Created Document attachment file on Drive: project:%s id:%s name:%s -> %s' % (
            self.project.id, self.id, self.filename, self.drive_id)


def connect_to_db(args):
    try:
        c = mdb.connect(host=args.dbserver,
                        user=args.dbuser,
                        passwd=args.dbpassword,
                        db=args.dbschema,
                        port=args.dbport, charset='utf8')
        return c
    except mdb.Error, e:
        print "Error %d: %s" % (e.args[0], e.args[1])
        sys.exit(1)


def create_sync_table(connection):
    cur = connection.cursor()
    cur.execute("""create table if not exists redmine_to_drive(
  id int NOT NULL ,
  type VARCHAR(30) NOT NULL ,
  drive_id VARCHAR(100) NOT NULL)""")


def connect_to_drive_service(args):
    storage = Storage("saved_user_creds.dat")
    credentials = storage.get()
    if credentials is None or credentials.invalid:
        credentials = run(client.flow_from_clientsecrets(
            'client_secrets.json',
            scope='https://www.googleapis.com/auth/drive',
            redirect_uri='urn:ietf:wg:oauth:2.0:oob'), storage)

    flow = client.flow_from_clientsecrets(
        'client_secrets.json',
        scope='https://www.googleapis.com/auth/drive',
        redirect_uri='urn:ietf:wg:oauth:2.0:oob'
    )
    http_auth = credentials.authorize(httplib2.Http())

    svc = discovery.build('drive', 'v2', http_auth)

    return svc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Syncronise redmine documents and redmine dmsf documents to a google drive folder")
    parser.add_argument('dbuser', help="mysql user")
    parser.add_argument('dbpassword', help="mysql password")
    parser.add_argument('dbserver', help="mysql server")
    parser.add_argument('dbport', help="mysql port", type=int, default=3306)
    parser.add_argument('dbschema', help="mysql schema")
    parser.add_argument('drive_root_dir', help="base folder on google drive")
    parser.add_argument('dmsf_dir', help="dmsf folder on redmine drive")
    parser.add_argument('documents_dir', help="documents folder on redmine drive")
    args = parser.parse_args()

    connection = connect_to_db(args)
    create_sync_table(connection)

    drive_service = connect_to_drive_service(args)

    project_collection = RedmineProjectCollection(args.drive_root_dir, connection, drive_service, args.dmsf_dir,
                                                  args.documents_dir)
    project_collection.lookup_remote_basedir_id()
    project_collection.load_from_db()
    project_collection.create_remote_project_folders()
