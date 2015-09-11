from apiclient import discovery
from oauth2client import client
from oauth2client.file import Storage
from oauth2client.tools import run
import httplib2


def connect_to_drive_service():
    storage = Storage("saved_user_creds.dat")
    credentials = storage.get()
    if credentials is None or credentials.invalid:
        credentials = run(client.flow_from_clientsecrets(
            'client_secrets.json',
            scope='https://www.googleapis.com/auth/drive',
            redirect_uri='urn:ietf:wg:oauth:2.0:oob'), storage)

    client.flow_from_clientsecrets(
        'client_secrets.json',
        scope='https://www.googleapis.com/auth/drive',
        redirect_uri='urn:ietf:wg:oauth:2.0:oob'
    )
    http_auth = credentials.authorize(httplib2.Http())

    svc = discovery.build('drive', 'v2', http_auth)

    return svc


drive_service = connect_to_drive_service()
