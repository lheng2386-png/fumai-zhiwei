"""Persistent document ingestion and Chinese/English BM25 retrieval, without model writes."""
import base64
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .core import InputError, utcnow

MAX_FILE = 6 * 1024 * 1024
MAX_TEXT = 300000
SUPPORTED = {'.txt', '.md', '.pdf', '.docx'}


def terms(text):
    text = text.lower()
    words = re.findall(r'[a-z0-9][a-z0-9_-]{1,48}', text)
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        words.extend(run[i:i+2] for i in range(len(run)-1))
        if len(run) == 1:
            words.append(run)
    return list(dict.fromkeys(words))[:3000]


def extract(filename, data):
    ext = Path(filename).suffix.lower()
    if ext in {'.txt', '.md'}:
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise InputError('无法读取文字编码。请将文件另存为 UTF-8 格式的 TXT 后重新上传。')
        pages = [('正文', text)]
    elif ext == '.docx':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = archive.getinfo('word/document.xml')
                if info.file_size > 4*1024*1024:
                    raise InputError('Word 正文过大，请分章上传。')
                xml = archive.read(info)
            if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
                raise InputError('Word 文件包含无法处理的结构，请另存为新文档后重试。')
            root = ElementTree.fromstring(xml)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            paragraphs = [''.join(t.text or '' for t in p.findall('.//w:t', ns)) for p in root.findall('.//w:p', ns)]
            pages = [('段落 '+str(i+1), text) for i, text in enumerate(paragraphs) if text.strip()]
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
            raise InputError('文件不是可读取的 DOCX。请用 Word 另存为 DOCX 或文字版 PDF 后重试。') from None
    elif ext == '.pdf':
        if not data.startswith(b'%PDF-'):
            raise InputError('文件内容不是 PDF，请检查文件后重新上传。')
        if not shutil.which('pdftotext'):
            raise InputError('服务器的 PDF 读取组件尚未安装，请联系管理员；当前可先上传 DOCX 或 TXT。')
        with tempfile.TemporaryDirectory(prefix='fumai-pdf-') as directory:
            file = Path(directory)/'document.pdf';file.write_bytes(data)
            try:
                output_file=Path(directory)/'document.txt'
                output = subprocess.run(['pdftotext', '-enc', 'UTF-8', '-layout', str(file), str(output_file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
                if output.returncode==0:
                    if output_file.stat().st_size>4*1024*1024:raise InputError('PDF 正文过大，请按章节拆分后上传。')
                    pdf_text=output_file.read_text(encoding='utf-8',errors='replace')
            except subprocess.TimeoutExpired:
                raise InputError('PDF 读取超过 25 秒，请按章节拆分后上传，或改用 DOCX。') from None
        if output.returncode:
            raise InputError('PDF 无法读取，可能已加密或损坏。请解除密码并另存后上传。')
        pages = [('第 '+str(i+1)+' 页', text) for i, text in enumerate(pdf_text.split('\f'))]
    else:
        raise InputError('支持 TXT、MD、DOCX 和文字版 PDF。请先转换文件格式。')
    if sum(len(text) for _, text in pages) > MAX_TEXT:
        raise InputError('正文超过 30 万字，请按章节拆分后上传。')
    if sum(len(re.sub(r'\s', '', text)) for _, text in pages) < 20:
        raise InputError('没有读到足够文字。如果是扫描件或图片 PDF，请先进行 OCR 文字识别，再上传文字版 PDF 或 DOCX。')
    return pages


class Knowledge:
    def __init__(self, store):
        self.store = store
        self.slots = threading.BoundedSemaphore(2)
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS kb_documents (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, filename TEXT NOT NULL,
                    digest TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, error TEXT,
                    created_at TEXT NOT NULL, started REAL NOT NULL, finished REAL, source BLOB,
                    chunk_count INTEGER NOT NULL DEFAULT 0, UNIQUE(owner,digest));
                CREATE TABLE IF NOT EXISTS kb_chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    location TEXT NOT NULL, text TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_kb_document ON kb_chunks(document_id,ordinal);
                CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(chunk_id UNINDEXED, tokens);
            ''')
            db.execute("UPDATE kb_documents SET status='failed',stage='interrupted',error=?,finished=? WHERE status='processing'",
                       ('服务重启导致建库中断，请点击重试；原文件已保留。', time.time()))

    def list(self, owner):
        with self.store.connect() as db:
            rows = db.execute('SELECT id,title,filename,status,stage,error,created_at,started,finished,chunk_count FROM kb_documents WHERE owner=? ORDER BY started DESC LIMIT 100', (owner,)).fetchall()
        return [{**dict(row), 'elapsed_seconds':max(0,int((row['finished'] or time.time())-row['started']))} for row in rows]

    def upload(self, owner, body):
        filename, encoded = body.get('filename'), body.get('content_base64')
        title = body.get('title') or filename
        if not isinstance(filename,str) or not 1 <= len(filename) <= 160 or '/' in filename or '\\' in filename or Path(filename).suffix.lower() not in SUPPORTED:
            raise InputError('请选择 TXT、MD、DOCX 或文字版 PDF 文件，文件名不超过 160 字。')
        if not isinstance(title,str) or not 1 <= len(title.strip()) <= 160:
            raise InputError('资料名称需要 1–160 个字符。')
        if not isinstance(encoded,str) or len(encoded) > MAX_FILE*4//3+8:
            raise InputError('文件不能超过 6 MB，请按章节拆分后上传。')
        try:
            data=base64.b64decode(encoded,validate=True)
        except ValueError:
            raise InputError('文件传输内容不完整，请重新选择文件后上传。') from None
        if not 1 <= len(data) <= MAX_FILE:
            raise InputError('请选择有内容且不超过 6 MB 的文件。')
        digest=hashlib.sha256(data).hexdigest()
        with self.store.connect() as db:
            existing=db.execute('SELECT id FROM kb_documents WHERE owner=? AND digest=?',(owner,digest)).fetchone()
        if existing:
            return self.get(owner,existing['id'])
        if not self.slots.acquire(False):
            raise InputError('已有两份资料正在建库，请等待其中一份完成后再上传。')
        doc_id=str(uuid.uuid4())
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                existing=db.execute('SELECT id FROM kb_documents WHERE owner=? AND digest=?',(owner,digest)).fetchone()
                if existing:
                    self.slots.release()
                    return self.get(owner,existing['id'])
                if db.execute('SELECT count(*) FROM kb_documents').fetchone()[0]>=500:
                    raise InputError('资料库已达到 500 份上限，请清理过期资料后再上传。')
                db.execute('INSERT INTO kb_documents(id,owner,title,filename,digest,status,stage,created_at,started,source) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (doc_id,owner,title.strip(),filename,digest,'processing','reading_document',utcnow(),time.time(),data))
            threading.Thread(target=self.ingest,args=(doc_id,),daemon=True,name='knowledge-ingest').start()
        except Exception:
            self.slots.release();raise
        return self.get(owner,doc_id)

    def ingest(self, doc_id):
        try:
            with self.store.connect() as db:
                row=db.execute('SELECT * FROM kb_documents WHERE id=?',(doc_id,)).fetchone()
            pages=extract(row['filename'],row['source'])
            with self.store.connect() as db:
                db.execute("UPDATE kb_documents SET stage='indexing' WHERE id=?",(doc_id,))
            chunks=[]
            for location,text in pages:
                text=text.replace('\x00','').strip()
                for start in range(0,len(text),800):
                    part=text[start:start+900].strip()
                    if part:chunks.append((str(uuid.uuid4()),doc_id,len(chunks)+1,location,part))
            if len(chunks)>1500:
                raise InputError('文档段落过多，请按章节拆分后上传。')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for chunk in chunks:
                    db.execute('INSERT INTO kb_chunks VALUES(?,?,?,?,?)',chunk)
                    db.execute('INSERT INTO kb_fts(chunk_id,tokens) VALUES(?,?)',(chunk[0],' '.join(terms(chunk[4]) + terms(row['title']))))
                db.execute("UPDATE kb_documents SET status='ready',stage='completed',finished=?,chunk_count=?,error=NULL WHERE id=?",(time.time(),len(chunks),doc_id))
        except Exception as exc:
            message=str(exc) if isinstance(exc,InputError) else '建库未完成，原文件已保留。请稍后重试；如持续失败，请联系管理员检查存储和文档读取组件。'
            with self.store.connect() as db:
                db.execute("UPDATE kb_documents SET status='failed',stage='failed',finished=?,error=? WHERE id=?",(time.time(),message,doc_id))
        finally:
            self.slots.release()

    def get(self,owner,doc_id):
        with self.store.connect() as db:
            row=db.execute('SELECT id,title,filename,status,stage,error,created_at,started,finished,chunk_count FROM kb_documents WHERE id=? AND owner=?',(doc_id,owner)).fetchone()
        if not row:raise InputError('资料不存在或无权访问。')
        return {**dict(row),'elapsed_seconds':max(0,int((row['finished'] or time.time())-row['started']))}

    def retry(self,owner,doc_id):
        self.get(owner,doc_id)
        if not self.slots.acquire(False):raise InputError('建库服务繁忙，请稍后重试。')
        try:
            with self.store.connect() as db:
                updated=db.execute("UPDATE kb_documents SET status='processing',stage='reading_document',error=NULL,started=?,finished=NULL WHERE id=? AND owner=? AND status='failed'",(time.time(),doc_id,owner)).rowcount
            if not updated:raise InputError('仅失败的资料可以重试。')
            threading.Thread(target=self.ingest,args=(doc_id,),daemon=True).start()
        except Exception:
            self.slots.release();raise
        return self.get(owner,doc_id)

    def delete(self,owner,doc_id):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM kb_documents WHERE id=? AND owner=?',(doc_id,owner)).fetchone()
            if not row:raise InputError('资料不存在或无权访问。')
            if row['status']=='processing':raise InputError('资料正在建库，请完成后再删除。')
            db.execute('DELETE FROM kb_fts WHERE chunk_id IN (SELECT id FROM kb_chunks WHERE document_id=?)',(doc_id,))
            db.execute('DELETE FROM kb_chunks WHERE document_id=?',(doc_id,))
            db.execute('DELETE FROM kb_documents WHERE id=? AND owner=?',(doc_id,owner))
        return {'deleted':True,'message':'资料已删除，后续检索不再使用；已有分析中的引用快照仍保留。'}

    def search(self,owner,query,limit=5):
        if not isinstance(query,str) or not 1<=len(query.strip())<=2000 or isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=8:
            raise InputError('请输入 1–2000 字的检索问题，返回条数为 1–8。')
        ignored={'知识','识库','查询','检索','关于','分析','什么','怎么','如何','需要','哪些','一下','资料','手册','根据','结合','问题','请问','请帮','这个'}
        tokens=[t for t in terms(query) if t not in ignored][:60]
        if not tokens:return {'chunks':[],'retrieval':'bm25','notice':'没有可检索的关键词，请输入设备型号、故障码或现象。'}
        match=' OR '.join('"'+t.replace('"','""')+'"' for t in tokens)
        with self.store.connect() as db:
            rows=db.execute('''SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.location,c.text,d.title,d.filename,d.digest,bm25(kb_fts) AS rank
                FROM kb_fts JOIN kb_chunks c ON c.id=kb_fts.chunk_id JOIN kb_documents d ON d.id=c.document_id
                WHERE kb_fts MATCH ? AND d.owner=? AND d.status='ready' ORDER BY rank,c.id LIMIT ?''',(match,owner,limit)).fetchall()
        return {'chunks':[{**dict(row),'citation_id':'KB:'+row['chunk_id']} for row in rows],'retrieval':'bm25',
                'notice':'以下为相关原文片段，需结合设备型号、文档版本与现场情况核对。' if rows else '知识库未找到相关资料，请上传适用的设备手册或补充具体故障码；不能据此编造结论。'}

    def chunk(self,owner,chunk_id):
        if not isinstance(chunk_id,str) or len(chunk_id)>64:raise InputError('无效的引用编号。')
        chunk_id=chunk_id.removeprefix('KB:')
        with self.store.connect() as db:
            row=db.execute('''SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.location,c.text,d.title,d.filename,d.digest
                FROM kb_chunks c JOIN kb_documents d ON d.id=c.document_id WHERE c.id=? AND d.owner=? AND d.status='ready' ''',(chunk_id,owner)).fetchone()
        if not row:raise InputError('引用段落不存在或无权访问，资料可能已删除。')
        return {**dict(row),'citation_id':'KB:'+row['chunk_id']}

    def preview(self,owner,doc_id):
        document=self.get(owner,doc_id)
        with self.store.connect() as db:
            rows=db.execute('SELECT id AS chunk_id,ordinal,location,text FROM kb_chunks WHERE document_id=? ORDER BY ordinal LIMIT 80',(doc_id,)).fetchall()
        return {'document':document,'chunks':[dict(row) for row in rows],'truncated':document['chunk_count']>80}

# Compatibility with the versioned general-knowledge and follow-up module.
from .knowledge_notes import answer, knowledge_context, guard_knowledge
