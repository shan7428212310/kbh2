from docx import Document
from flask import Flask, request, jsonify
import tempfile
import os
import re
import PyPDF2
import logging
import http.client
import urllib.parse
from whoosh.index import create_in, open_dir, EmptyIndexError
from whoosh.fields import Schema, TEXT, ID
from whoosh.qparser import QueryParser
from whoosh.analysis import StemmingAnalyzer

connection_string = 'DefaultEndpointsProtocol=https;AccountName=kbhdocumentstorage;AccountKey=doSuaslyxCWTQRhiKeyTQEIaT+wVsx4upRJmmNOicvGcb5vJCb1S5d+0bsNQitQxI4uVbYtTwcT1+AStUfrp0Q==;EndpointSuffix=core.windows.net'
container_name = 'kbhdocumentcontainer'
app = Flask(__name__)

@app.route('/')
def index():
    return "Hello, world!"

@app.route('/hello')
def hello():
    name = request.args.get('name', 'World')
    return f"Hello, {name}!"

def download_blob_to_temp_file(blob_url, temp_file_path):
    parsed_url = urllib.parse.urlparse(blob_url)
    conn = http.client.HTTPSConnection(parsed_url.netloc)
    conn.request("GET", parsed_url.path)
    response = conn.getresponse()
    with open(temp_file_path, 'wb') as temp_file:
        temp_file.write(response.read())
def process_docx(file_path):
    doc = Document(file_path)
    full_text = []
    for para in doc.paragraphs:
        full_text.append(para.text)
    return '\n'.join(full_text)
def create_index_and_upload(connection_string, container_name):
    list_blobs_url = f"{connection_string.rstrip(';')}/{container_name}?restype=container&comp=list"
    parsed_url = urllib.parse.urlparse(list_blobs_url)
    conn = http.client.HTTPSConnection(parsed_url.netloc)
    conn.request("GET", parsed_url.path + '?' + parsed_url.query)
    response = conn.getresponse()

    if response.status != 200:
        logging.error(f"Failed to list blobs: {response.status} - {response.read().decode('utf-8')}")
        return

    blob_names = re.findall(r'<Name>(.*?)</Name>', response.read().decode('utf-8'))
    for blob_name in blob_names:
        blob_url = f"{connection_string.rstrip(';')}/{container_name}/{blob_name}"
        if blob_name.startswith('~$') or not (blob_name.lower().endswith(".docx") or blob_name.lower().endswith(".pdf")):
            continue

        try:
            temp_file_path = tempfile.NamedTemporaryFile(delete=False).name
            download_blob_to_temp_file(blob_url, temp_file_path)

            if blob_name.lower().endswith(".docx"):
                text = process_docx(temp_file_path)
            elif blob_name.lower().endswith(".pdf"):
                with open(temp_file_path, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    text = ""
                    for page in pdf_reader.pages:
                        text += page.extract_text()

            schema = Schema(title=TEXT(stored=True), path=ID(stored=True), content=TEXT(stored=True, analyzer=StemmingAnalyzer()))
            ix = create_in(tempfile.mkdtemp(), schema)
            writer = ix.writer()
            writer.add_document(title=blob_name, path=blob_name, content=text)
            writer.commit()

            os.remove(temp_file_path)

        except Exception as e:
            logging.error(f"Failed to process {blob_name}: {e}")

def download_index_from_blob(connection_string, container_name, temp_index_dir):
    blob_service_url = f"https://{connection_string.split(';')[1].split('=')[1]}.blob.core.windows.net"
    list_blobs_url = f"{blob_service_url}/{container_name}?restype=container&comp=list"

    parsed_url = urllib.parse.urlparse(list_blobs_url)
    conn = http.client.HTTPSConnection(parsed_url.netloc)
    conn.request("GET", parsed_url.path + '?' + parsed_url.query, headers={"x-ms-version": "2020-08-04"})
    response = conn.getresponse()

    if response.status != 200:
        logging.error(f"Failed to list blobs: {response.status} - {response.read().decode('utf-8')}")
        return

    blobs = re.findall(r'<Name>(.*?)</Name>', response.read().decode('utf-8'))
    for blob_name in blobs:
        blob_url = f"{blob_service_url}/{container_name}/{urllib.parse.quote(blob_name)}"  # Encode blob name
        download_blob_url = f"{blob_url}?{connection_string.split(';', 1)[1]}"
        download_file_path = os.path.join(temp_index_dir, blob_name)
        os.makedirs(os.path.dirname(download_file_path), exist_ok=True)

        conn.request("GET", urllib.parse.urlparse(download_blob_url).path + '?' + urllib.parse.urlparse(download_blob_url).query)
        response = conn.getresponse()

        if response.status == 200:
            with open(download_file_path, "wb") as download_file:
                download_file.write(response.read())
        else:
            logging.error(f"Failed to download {blob_name}: {response.status} - {response.read().decode('utf-8')}")


def search_index(query_str, connection_string, container_name, temp_index_dir):
    try:
        download_index_from_blob(connection_string, container_name, temp_index_dir)
        ix = open_dir(temp_index_dir)
        searcher = ix.searcher()
        query = QueryParser("content", schema=ix.schema).parse(query_str)

        results = searcher.search(query, limit=None)
        hits = []
        for hit in results:
            matched_para = re.sub('<.*?>', '', hit.highlights("content", top=4))
            hits.append({"path": hit['path'], "paragraphs": matched_para.replace('\n', '').replace('\t', '')})

        searcher.close()
        ix.close()

        return hits

    except EmptyIndexError as e:
        logging.error(f"EmptyIndexError: {e}")
        return []

@app.route('/search', methods=['GET'])
def search():
    query_str = request.args.get('q', '')
    with tempfile.TemporaryDirectory() as temp_index_dir:
        results = search_index(query_str, connection_string, container_name, temp_index_dir)
        return jsonify(results)

if __name__ == '__main__':
    app.run()
