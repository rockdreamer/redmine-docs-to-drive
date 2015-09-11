# redmine-docs-to-drive
Export DMSF and Documents attachments from redmine to Google Drive

sync.py is a quick-and-dirty export script that will migrate all DMSF and Documents files uploaded on a Redmine instance to Google Drive

Requires:
* access to the redmine dmsf directory
* access to the redmine files directory
* access to the redmine database
* a Google API key with access to the Drive API (that must be obtained from the Google developers console)

A better option is to use the redmine_to_drive celery app to upload all DMSF File revisions and all Documents attachments to folders

requirements.txt contains all necessary requirements for celery and MySQL.

Copy celeryconfig.py.sample to celeryconfig.py and adjust according to your requirements

Start a worker with:

celery worker -A redmine_to_drive -l INFO -f worker.log 

Start or restart the migration with:

celery -A redmine_to_drive call redmine_to_drive.update_project_tree_structure

No guarantees provided
