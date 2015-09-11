# coding: utf-8
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, text, \
    ForeignKey
from sqlalchemy.orm import relationship, backref
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()
metadata = Base.metadata


class RedmineToDriveMapping(Base):
    __tablename__ = 'redmine_to_drive_mapping'
    __table_args__ = (
        Index('index_mapping_on_redmine_id_and_type', 'mapping_type', 'redmine_id', unique=True),
    )

    id = Column(Integer, primary_key=True)
    mapping_type = Column(String(255))
    drive_id = Column(String(255))
    redmine_id = Column(Integer)
    last_update = Column(DateTime)

    __mapper_args__ = {
        'polymorphic_on': mapping_type,
        'polymorphic_identity': 'redmine_to_drive_mapping',
        'with_polymorphic': '*'
    }


class RedmineDmsfFolderToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'dmsf_folder'}


class RedmineDmsfFileToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'dmsf_file'}


class RedmineDmsfFileRevisionToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'dmsf_file_revision'}


class RedmineBasedirToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'basedir'}


class RedmineProjectToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'project'}


class RedmineProjectDocumentsToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'project_docs'}


class RedmineProjectDmsfToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'project_dmsf'}


class RedmineDocumentToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'document'}


class RedmineDocumentAttachmentToDriveMapping(RedmineToDriveMapping):
    __mapper_args__ = {'polymorphic_identity': 'document_attachment'}


class Attachment(Base):
    __tablename__ = 'attachments'

    id = Column(Integer, primary_key=True)
    container_type = Column(String(30))
    container_id = Column(Integer)
    filename = Column(String(255), nullable=False, server_default=text("''"))
    disk_filename = Column(String(255), nullable=False, server_default=text("''"))
    filesize = Column(Integer, nullable=False, server_default=text("'0'"))
    content_type = Column(String(255), server_default=text("''"))
    digest = Column(String(40), nullable=False, server_default=text("''"))
    downloads = Column(Integer, nullable=False, server_default=text("'0'"))
    author_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True, server_default=text("'0'"))
    created_on = Column(DateTime, index=True)
    description = Column(String(255))
    disk_directory = Column(String(255))
    __mapper_args__ = {'polymorphic_on': container_type}


class DocumentAttachment(Attachment):
    __mapper_args__ = {'polymorphic_identity': 'Document'}
    document = relationship("Document",
                            backref="attachments",
                            primaryjoin="Document.id == DocumentAttachment.container_id",
                            foreign_keys='DocumentAttachment.container_id')
    drive = relationship("RedmineDocumentAttachmentToDriveMapping",
                         backref="attachment",
                         primaryjoin="RedmineDocumentAttachmentToDriveMapping.redmine_id == DocumentAttachment.id",
                         foreign_keys='RedmineDocumentAttachmentToDriveMapping.redmine_id')


class DmsfFileRevision(Base):
    __tablename__ = 'dmsf_file_revisions'

    id = Column(Integer, primary_key=True)
    dmsf_file_id = Column(Integer, ForeignKey('dmsf_files.id'), nullable=False)
    source_dmsf_file_revision_id = Column(Integer, ForeignKey('dmsf_file_revisions.id'))
    name = Column(String(255), nullable=False)
    dmsf_folder_id = Column(Integer, ForeignKey('dmsf_folders.id'))
    disk_filename = Column(String(255), nullable=False)
    size = Column(Integer)
    mime_type = Column(String(255))
    title = Column(String(255))
    description = Column(Text)
    workflow = Column(Integer)
    major_version = Column(Integer, nullable=False)
    minor_version = Column(Integer, nullable=False)
    comment = Column(Text)
    deleted = Column(Integer, nullable=False, server_default=text("'0'"))
    deleted_by_user_id = Column(Integer)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    drive = relationship("RedmineDmsfFileRevisionToDriveMapping",
                         backref="file_revision",
                         primaryjoin="RedmineDmsfFileRevisionToDriveMapping.redmine_id == DmsfFileRevision.id",
                         foreign_keys='RedmineDmsfFileRevisionToDriveMapping.redmine_id')


class DmsfFile(Base):
    __tablename__ = 'dmsf_files'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    dmsf_folder_id = Column(Integer, ForeignKey('dmsf_folders.id'))
    name = Column(String(255), nullable=False)
    notification = Column(Integer, nullable=False, server_default=text("'0'"))
    deleted = Column(Integer, nullable=False, server_default=text("'0'"))
    deleted_by_user_id = Column(Integer, ForeignKey('users.id'))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    revisions = relationship('DmsfFileRevision', backref='dmsf_file',
                             order_by='DmsfFileRevision.major_version, DmsfFileRevision.minor_version')
    drive = relationship("RedmineDmsfFileToDriveMapping",
                         backref="file",
                         primaryjoin="RedmineDmsfFileToDriveMapping.redmine_id == DmsfFile.id",
                         foreign_keys='RedmineDmsfFileToDriveMapping.redmine_id')


class DmsfFolder(Base):
    __tablename__ = 'dmsf_folders'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    dmsf_folder_id = Column(Integer, ForeignKey('dmsf_folders.id'))
    title = Column(String(255), nullable=False)
    description = Column(Text)
    notification = Column(Integer, nullable=False, server_default=text("'0'"))
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    child_folders = relationship("DmsfFolder", backref=backref('parent', remote_side=[id]))
    files = relationship('DmsfFile', backref=backref('folder', remote_side=[id]), order_by='DmsfFile.id')
    revisions = relationship('DmsfFileRevision', backref=backref('folder', remote_side=[id]),
                             order_by='DmsfFileRevision.id')
    drive = relationship("RedmineDmsfFolderToDriveMapping",
                         backref="file_revision",
                         primaryjoin="RedmineDmsfFolderToDriveMapping.redmine_id == DmsfFolder.id",
                         foreign_keys='RedmineDmsfFolderToDriveMapping.redmine_id')


class Document(Base):
    __tablename__ = 'documents'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True, server_default=text("'0'"))
    category_id = Column(Integer, nullable=False, index=True, server_default=text("'0'"))
    title = Column(String(60), nullable=False, server_default=text("''"))
    description = Column(Text)
    created_on = Column(DateTime, index=True)
    drive = relationship("RedmineDocumentToDriveMapping",
                         backref="document",
                         primaryjoin="RedmineDocumentToDriveMapping.redmine_id == Document.id",
                         foreign_keys='RedmineDocumentToDriveMapping.redmine_id')


class MemberRole(Base):
    __tablename__ = 'member_roles'

    id = Column(Integer, primary_key=True)
    member_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    role_id = Column(Integer, nullable=False, index=True)
    inherited_from = Column(Integer)


class Member(Base):
    __tablename__ = 'members'
    __table_args__ = (
        Index('index_members_on_user_id_and_project_id', 'user_id', 'project_id', unique=True),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True, server_default=text("'0'"))
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True, server_default=text("'0'"))
    created_on = Column(DateTime)
    mail_notification = Column(Integer, nullable=False, server_default=text("'0'"))
    dmsf_mail_notification = Column(Integer)


class Project(Base):
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, server_default=text("''"))
    description = Column(Text)
    homepage = Column(String(255), server_default=text("''"))
    is_public = Column(Integer, nullable=False, server_default=text("'1'"))
    parent_id = Column(Integer, ForeignKey('projects.id'))
    created_on = Column(DateTime)
    updated_on = Column(DateTime)
    identifier = Column(String(255))
    status = Column(Integer, nullable=False, server_default=text("'1'"))
    lft = Column(Integer, index=True)
    rgt = Column(Integer, index=True)
    dmsf_description = Column(Text)
    inherit_members = Column(Integer, nullable=False, server_default=text("'0'"))
    documents = relationship('Document', backref='project', order_by='Document.id')
    dmsf_folders = relationship("DmsfFolder", backref="project")
    revisions = relationship('DmsfFileRevision', backref='project',
                             order_by='DmsfFileRevision.major_version, DmsfFileRevision.minor_version')

    child_projects = relationship("Project", backref=backref('parent', remote_side=[id]))
    drive_project = relationship("RedmineProjectToDriveMapping",
                                 backref="project",
                                 primaryjoin="RedmineProjectToDriveMapping.redmine_id == Project.id",
                                 foreign_keys='RedmineProjectToDriveMapping.redmine_id')
    drive_documents = relationship("RedmineProjectDocumentsToDriveMapping",
                                   backref="project",
                                   primaryjoin="RedmineProjectDocumentsToDriveMapping.redmine_id == Project.id",
                                   foreign_keys='RedmineProjectDocumentsToDriveMapping.redmine_id')
    drive_dmsf = relationship("RedmineProjectDmsfToDriveMapping",
                              backref="project",
                              primaryjoin="RedmineProjectDmsfToDriveMapping.redmine_id == Project.id",
                              foreign_keys='RedmineProjectDmsfToDriveMapping.redmine_id')


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (
        Index('index_users_on_id_and_type', 'id', 'type'),
    )

    id = Column(Integer, primary_key=True)
    login = Column(String(255), nullable=False, server_default=text("''"))
    hashed_password = Column(String(40), nullable=False, server_default=text("''"))
    firstname = Column(String(30), nullable=False, server_default=text("''"))
    lastname = Column(String(255), nullable=False, server_default=text("''"))
    mail = Column(String(60), nullable=False, server_default=text("''"))
    admin = Column(Integer, nullable=False, server_default=text("'0'"))
    status = Column(Integer, nullable=False, server_default=text("'1'"))
    last_login_on = Column(DateTime)
    language = Column(String(5), server_default=text("''"))
    auth_source_id = Column(Integer, index=True)
    created_on = Column(DateTime)
    updated_on = Column(DateTime)
    type = Column(String(255), index=True)
    identity_url = Column(String(255))
    mail_notification = Column(String(255), nullable=False, server_default=text("''"))
    salt = Column(String(64))
