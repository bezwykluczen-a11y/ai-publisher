# AI Publisher API — research workflow and local deletion.
from app.article_images import (
    router as article_images_router,
    init_image_db,
    image_before_delete,
    remove_local_image,
)
from enum import Enum
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import psycopg
import redis
from redis import Redis
from rq import Queue, Retry
from rq.job import Job
from rq.exceptions import NoSuchJobError
from rq.registry import StartedJobRegistry, ScheduledJobRegistry, DeferredJobRegistry
from psycopg.types.json import Jsonb
import logging
logger = logging.getLogger(__name__)
app = FastAPI(title='AI Publisher API')
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:3001'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
DATABASE_URL = os.getenv('DATABASE_URL')
REDIS_URL = os.getenv('REDIS_URL')
redis_conn = Redis.from_url(REDIS_URL)
task_queue = Queue(connection=redis_conn)

class RunStatus(str, Enum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'

class ProjectCreate(BaseModel):
    name: str
    domain: str
    keywords: str | None = None

class ProjectSettings(BaseModel):
    language: str = Field(default='pl-PL', min_length=2, max_length=35)
    audience: str = Field(default='', max_length=2000)
    brand_style: str = Field(default='', max_length=4000)

class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content_md: str = Field(min_length=1, max_length=50000)
    language: str = Field(default='pl-PL', min_length=2, max_length=35)
    source_url: str | None = Field(default=None, max_length=2000)

def init_db():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('\n                CREATE TABLE IF NOT EXISTS projects (\n                    id SERIAL PRIMARY KEY,\n                    name TEXT NOT NULL,\n                    domain TEXT NOT NULL,\n                    created_at TIMESTAMP DEFAULT NOW()\n                );\n            ')
            cur.execute("\n                CREATE TABLE IF NOT EXISTS runs (\n                    id SERIAL PRIMARY KEY,\n                    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,\n                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),\n                    started_at TIMESTAMP DEFAULT NOW(),\n                    finished_at TIMESTAMP NULL\n                );\n            ")
            cur.execute('\n                CREATE TABLE IF NOT EXISTS articles (\n                    id SERIAL PRIMARY KEY,\n                    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,\n                    title TEXT NOT NULL,\n                    content TEXT NOT NULL,\n                    llm_provider TEXT NOT NULL,\n                    llm_model TEXT NOT NULL,\n                    created_at TIMESTAMP DEFAULT NOW()\n                );\n            ')
            cur.execute("\n                DO $$\n                BEGIN\n                    IF NOT EXISTS (\n                        SELECT 1 FROM pg_constraint WHERE conname = 'unique_project_name_domain'\n                    ) THEN\n                        ALTER TABLE projects\n                        ADD CONSTRAINT unique_project_name_domain UNIQUE (name, domain);\n                    END IF;\n                END $$;\n            ")
            cur.execute('\n                ALTER TABLE projects\n                ADD COLUMN IF NOT EXISTS keywords TEXT;\n            ')
            cur.execute('\n                ALTER TABLE articles\n                ADD COLUMN IF NOT EXISTS wordpress_post_id INTEGER;\n            ')
            cur.execute('\n                ALTER TABLE articles\n                ADD COLUMN IF NOT EXISTS wordpress_url TEXT;\n            ')
            cur.execute("\n                ALTER TABLE articles\n                ADD COLUMN IF NOT EXISTS publication_status TEXT NOT NULL DEFAULT 'not_published';\n            ")
            cur.execute('\n                ALTER TABLE articles\n                ADD COLUMN IF NOT EXISTS published_at TIMESTAMP NULL;\n            ')
            cur.execute("\n                ALTER TABLE projects ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'pl-PL';\n                ALTER TABLE projects ADD COLUMN IF NOT EXISTS audience TEXT NOT NULL DEFAULT '';\n                ALTER TABLE projects ADD COLUMN IF NOT EXISTS brand_style TEXT NOT NULL DEFAULT '';\n                CREATE TABLE IF NOT EXISTS knowledge_documents (\n                    id SERIAL PRIMARY KEY,\n                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,\n                    title TEXT NOT NULL,\n                    content_md TEXT NOT NULL,\n                    language TEXT NOT NULL DEFAULT 'pl-PL',\n                    source_url TEXT,\n                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()\n                );\n                CREATE INDEX IF NOT EXISTS knowledge_documents_project_idx\n                    ON knowledge_documents(project_id);\n            ")
            cur.execute("\n                CREATE TABLE IF NOT EXISTS research_runs (\n                    id SERIAL PRIMARY KEY,\n                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,\n                    query TEXT NOT NULL,\n                    provider TEXT NOT NULL DEFAULT 'serper',\n                    status TEXT NOT NULL DEFAULT 'queued'\n                        CHECK (status IN ('queued', 'running', 'completed', 'failed')),\n                    target_count INTEGER NOT NULL DEFAULT 50,\n                    unique_count INTEGER NOT NULL DEFAULT 0,\n                    report JSONB,\n                    error TEXT,\n                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n                    finished_at TIMESTAMPTZ NULL\n                );\n                CREATE INDEX IF NOT EXISTS research_runs_project_idx\n                    ON research_runs(project_id);\n            ")
            cur.execute('\n                ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS brief JSONB;\n                ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS content_job_id TEXT;\n                ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS brief_job_id TEXT;\n                ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS article_job_id TEXT;\n                ALTER TABLE runs ADD COLUMN IF NOT EXISTS research_id INTEGER REFERENCES research_runs(id) ON DELETE SET NULL;\n                ALTER TABLE runs ADD COLUMN IF NOT EXISTS research_context JSONB;\n                CREATE TABLE IF NOT EXISTS research_pages (\n                    id SERIAL PRIMARY KEY,\n                    research_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,\n                    url TEXT NOT NULL, title TEXT NOT NULL, content_text TEXT NOT NULL,\n                    char_count INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n                    UNIQUE(research_id, url)\n                );\n                CREATE INDEX IF NOT EXISTS research_pages_research_idx ON research_pages(research_id);\n            ')
        conn.commit()

@app.on_event('startup')
def on_startup():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL is not set')
    if not REDIS_URL:
        raise RuntimeError('REDIS_URL is not set')
    init_db()
    init_image_db()

@app.get('/')
def root():
    return {'message': 'AI Publisher API is running'}

@app.get('/health')
def health():
    return {'status': 'ok', 'environment': os.getenv('ENVIRONMENT', 'development')}

@app.get('/db-ping')
def db_ping():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
            result = cur.fetchone()[0]
    return {'db': 'ok', 'result': result}

@app.get('/redis-ping')
def redis_ping():
    r = redis.from_url(REDIS_URL, decode_responses=True)
    result = r.ping()
    return {'redis': 'ok', 'result': result}

@app.get('/system/status')
def system_status():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
            db_result = cur.fetchone()[0]
    r = redis.from_url(REDIS_URL, decode_responses=True)
    redis_result = r.ping()
    return {'api': 'ok', 'db': db_result == 1, 'redis': redis_result is True}

@app.get('/projects')
def get_projects():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('\n                SELECT id, name, domain, keywords, created_at\n                FROM projects\n                ORDER BY id ASC\n            ')
            rows = cur.fetchall()
    data = [{'id': row[0], 'name': row[1], 'domain': row[2], 'keywords': row[3], 'created_at': row[4].isoformat() if row[4] else None} for row in rows]
    return JSONResponse(content=data, media_type='application/json; charset=utf-8')

@app.post('/projects')
def create_project(project: ProjectCreate):
    name = project.name.strip()
    domain = project.domain.strip()
    keywords = project.keywords.strip() if project.keywords else None
    if not name:
        raise HTTPException(status_code=400, detail='Project name cannot be empty.')
    if not domain:
        raise HTTPException(status_code=400, detail='Domain cannot be empty.')
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM projects WHERE name = %s OR domain = %s', (name, domain))
            existing = cur.fetchone()
            if existing:
                raise HTTPException(status_code=409, detail='A project with this name or domain already exists.')
            cur.execute('\n                INSERT INTO projects (name, domain, keywords)\n                VALUES (%s, %s, %s)\n                RETURNING id, name, domain, keywords, created_at\n            ', (name, domain, keywords))
            row = cur.fetchone()
        conn.commit()
    return JSONResponse(content={'id': row[0], 'name': row[1], 'domain': row[2], 'keywords': row[3], 'created_at': row[4].isoformat() if row[4] else None}, media_type='application/json; charset=utf-8')

@app.post('/projects/seed')
def seed_projects():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("\n                INSERT INTO projects (name, domain)\n                VALUES\n                ('Demo SEO Project', 'example.com'),\n                ('AI Publisher Sandbox', 'sandbox.local')\n                ON CONFLICT (name, domain) DO NOTHING\n                RETURNING id, name, domain, created_at\n            ")
            rows = cur.fetchall()
            conn.commit()
    return [{'id': row[0], 'name': row[1], 'domain': row[2], 'created_at': row[3].isoformat() if row[3] else None} for row in rows]

@app.get('/runs')
def get_runs():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('\n                SELECT r.id, r.project_id, p.name, r.status, r.started_at, r.finished_at\n                FROM runs r\n                JOIN projects p ON p.id = r.project_id\n                ORDER BY r.id DESC\n            ')
            rows = cur.fetchall()
    return [{'id': row[0], 'project_id': row[1], 'project_name': row[2], 'status': row[3], 'started_at': row[4].isoformat() if row[4] else None, 'finished_at': row[5].isoformat() if row[5] else None} for row in rows]

@app.post('/runs/start')
def start_run(project_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, name FROM projects WHERE id = %s', (project_id,))
            project = cur.fetchone()
            if not project:
                raise HTTPException(status_code=404, detail='Project not found')
            cur.execute('\n                INSERT INTO runs (project_id, status)\n                VALUES (%s, %s)\n                RETURNING id, project_id, status, started_at, finished_at\n            ', (project_id, RunStatus.QUEUED.value))
            row = cur.fetchone()
        conn.commit()
    task_queue.enqueue('app.worker.process_run', row[0], retry=Retry(max=3, interval=[10, 30, 60]), job_timeout=300)
    return {'id': row[0], 'project_id': row[1], 'status': row[2], 'started_at': row[3].isoformat() if row[3] else None, 'finished_at': row[4].isoformat() if row[4] else None}

@app.post('/runs/{run_id}/status')
def update_run_status(run_id: int, status: RunStatus):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, project_id, status, started_at, finished_at FROM runs WHERE id = %s', (run_id,))
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail='Run not found')
            if status == RunStatus.COMPLETED:
                cur.execute('\n                    UPDATE runs\n                    SET status = %s, finished_at = NOW()\n                    WHERE id = %s\n                    RETURNING id, project_id, status, started_at, finished_at\n                ', (status.value, run_id))
            else:
                cur.execute('\n                    UPDATE runs\n                    SET status = %s\n                    WHERE id = %s\n                    RETURNING id, project_id, status, started_at, finished_at\n                ', (status.value, run_id))
            row = cur.fetchone()
        conn.commit()
    return {'id': row[0], 'project_id': row[1], 'status': row[2], 'started_at': row[3].isoformat() if row[3] else None, 'finished_at': row[4].isoformat() if row[4] else None}

@app.post('/projects/{project_id}/research/start')
def start_project_research(project_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, keywords FROM projects WHERE id = %s', (project_id,))
            project = cur.fetchone()
            if not project:
                raise HTTPException(status_code=404, detail='Project not found')
            project_id, query = project
            if not query or not query.strip():
                raise HTTPException(status_code=400, detail='Project keywords are required to start research.')
            query = query.strip()
            cur.execute("\n                INSERT INTO research_runs\n                    (project_id, query, provider, status, target_count)\n                VALUES (%s, %s, 'serper', 'queued', 50)\n                RETURNING id, project_id, query, status, target_count, created_at\n            ", (project_id, query))
            row = cur.fetchone()
        conn.commit()
    research_id = row[0]
    try:
        job = task_queue.enqueue('app.worker.start_serp_research', research_id, retry=Retry(max=2, interval=[15, 45]), job_timeout=600)
    except Exception:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE research_runs SET status = 'failed', error = %s WHERE id = %s", ('Could not enqueue research job', research_id))
            conn.commit()
        raise
    return JSONResponse(content={'research_id': research_id, 'project_id': project_id, 'query': query, 'status': row[3], 'target_count': row[4], 'job_id': job.id}, media_type='application/json; charset=utf-8')

@app.get('/research/{research_id}')
def get_research(research_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('\n                SELECT id, project_id, query, provider, status,\n                       target_count, unique_count, report, error,\n                       created_at, finished_at, brief, content_job_id, brief_job_id, article_job_id\n                FROM research_runs\n                WHERE id = %s\n            ', (research_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Research not found')
    return JSONResponse(content={'id': row[0], 'project_id': row[1], 'query': row[2], 'provider': row[3], 'status': row[4], 'target_count': row[5], 'unique_count': row[6], 'report': row[7], 'brief': row[11], 'content_job_id': row[12], 'brief_job_id': row[13], 'article_job_id': row[14], 'error': row[8], 'created_at': row[9].isoformat() if row[9] else None, 'finished_at': row[10].isoformat() if row[10] else None}, media_type='application/json; charset=utf-8')

def article_response(row):
    return {'id': row[0], 'run_id': row[1], 'title': row[2], 'content': row[3], 'llm_provider': row[4], 'llm_model': row[5], 'created_at': row[6].isoformat() if row[6] else None, 'wordpress_post_id': row[7], 'wordpress_url': row[8], 'publication_status': row[9], 'published_at': row[10].isoformat() if row[10] else None}

@app.get('/runs/{run_id}/articles')
def get_run_articles(run_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('\n                SELECT id, run_id, title, content, llm_provider, llm_model,\n                       created_at, wordpress_post_id, wordpress_url,\n                       publication_status, published_at\n                FROM articles\n                WHERE run_id = %s\n                ORDER BY id DESC\n            ', (run_id,))
            rows = cur.fetchall()
    return [article_response(row) for row in rows]

@app.get('/articles')
def get_all_articles():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('\n                SELECT a.id, a.run_id, a.title, a.content, a.llm_provider,\n                       a.llm_model, a.created_at, a.wordpress_post_id,\n                       a.wordpress_url, a.publication_status, a.published_at,\n                       p.name\n                FROM articles a\n                JOIN runs r ON r.id = a.run_id\n                JOIN projects p ON p.id = r.project_id\n                ORDER BY a.id DESC\n            ')
            rows = cur.fetchall()
    return [{**article_response(row[:11]), 'project_name': row[11]} for row in rows]

@app.post('/articles/{article_id}/publish')
def publish_article_endpoint(article_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT publication_status FROM articles WHERE id=%s FOR UPDATE', (article_id,))
            article = cur.fetchone()
            if not article:
                raise HTTPException(404, 'Article not found')
            if article[0] in {'pending', 'draft_created'} or article_has_active_publish_job(article_id):
                raise HTTPException(409, 'Artykuł jest już wysyłany lub szkic został utworzony.')
            cur.execute("UPDATE articles SET publication_status='pending' WHERE id=%s", (article_id,))
        conn.commit()
    try:
        job = task_queue.enqueue('app.worker.publish_article', article_id, retry=Retry(max=2, interval=[10, 30]), job_timeout=120)
    except Exception:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE articles SET publication_status = 'not_published' WHERE id = %s", (article_id,))
            conn.commit()
        raise
    return {'article_id': article_id, 'status': 'queued', 'job_id': job.id, 'publication_status': 'pending', 'wordpress_status': 'draft'}

@app.get('/projects/{project_id}/settings')
def get_project_settings(project_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, language, audience, brand_style FROM projects WHERE id = %s', (project_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Project not found')
    return dict(zip(('project_id', 'language', 'audience', 'brand_style'), row))

@app.put('/projects/{project_id}/settings')
def update_project_settings(project_id: int, settings: ProjectSettings):
    language = settings.language.strip()
    if not language:
        raise HTTPException(status_code=400, detail='Language cannot be empty')
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('\n                UPDATE projects SET language = %s, audience = %s, brand_style = %s\n                WHERE id = %s RETURNING id, language, audience, brand_style\n                ', (language, settings.audience.strip(), settings.brand_style.strip(), project_id))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='Project not found')
        conn.commit()
    return dict(zip(('project_id', 'language', 'audience', 'brand_style'), row))

@app.get('/projects/{project_id}/knowledge')
def get_project_knowledge(project_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM projects WHERE id = %s', (project_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail='Project not found')
            cur.execute('\n                SELECT id, project_id, title, content_md, language, source_url,\n                       created_at, updated_at\n                FROM knowledge_documents WHERE project_id = %s ORDER BY id DESC\n                ', (project_id,))
            rows = cur.fetchall()
    return [{'id': row[0], 'project_id': row[1], 'title': row[2], 'content_md': row[3], 'language': row[4], 'source_url': row[5], 'created_at': row[6].isoformat(), 'updated_at': row[7].isoformat()} for row in rows]

@app.post('/projects/{project_id}/knowledge', status_code=201)
def create_project_knowledge(project_id: int, document: KnowledgeCreate):
    title = document.title.strip()
    content = document.content_md.strip()
    language = document.language.strip()
    source_url = document.source_url.strip() or None if document.source_url else None
    if not title or not content or (not language):
        raise HTTPException(status_code=400, detail='Title, content and language cannot be empty')
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM projects WHERE id = %s', (project_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail='Project not found')
            cur.execute('\n                INSERT INTO knowledge_documents\n                    (project_id, title, content_md, language, source_url)\n                VALUES (%s, %s, %s, %s, %s)\n                RETURNING id, created_at\n                ', (project_id, title, content, language, source_url))
            row = cur.fetchone()
        conn.commit()
    return {'id': row[0], 'project_id': project_id, 'title': title, 'language': language, 'source_url': source_url, 'created_at': row[1].isoformat()}
_ACTIVE = {'queued', 'started', 'deferred', 'scheduled'}

def job_state(job_id):
    if not job_id:
        return None
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        status = job.get_status(refresh=True)
        status = getattr(status, 'value', status)
        error = 'Zadanie zakończyło się błędem. Sprawdź logi workera.' if status in {'failed', 'stopped', 'canceled'} else None
        return {'job_id': job_id, 'status': status, 'error': error}
    except NoSuchJobError:
        return {'job_id': job_id, 'status': 'expired', 'error': 'Status zadania wygasł; sprawdź zapisany wynik.'}

@app.get('/jobs/{job_id}')
def get_job_status(job_id: str):
    return job_state(job_id)

@app.get('/research/{research_id}/pages')
def get_research_pages(research_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        if not conn.execute('SELECT id FROM research_runs WHERE id=%s', (research_id,)).fetchone():
            raise HTTPException(404, 'Research not found')
        rows = conn.execute('SELECT id,research_id,url,title,content_text,char_count,created_at FROM research_pages WHERE research_id=%s ORDER BY id', (research_id,)).fetchall()
    return [{'id': r[0], 'research_id': r[1], 'url': r[2], 'title': r[3], 'content_text': r[4], 'char_count': r[5], 'created_at': r[6].isoformat()} for r in rows]

def enqueue_research_phase(research_id, phase):
    fields = {'content': 'content_job_id', 'brief': 'brief_job_id'}
    field = fields[phase]
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status,content_job_id,brief_job_id,article_job_id FROM research_runs WHERE id=%s FOR UPDATE', (research_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, 'Research not found')
            if row[0] != 'completed':
                raise HTTPException(409, 'Research must be completed')
            for id in row[1:]:
                state = job_state(id)
                if state and state['status'] in _ACTIVE:
                    raise HTTPException(409, 'Inne zadanie tego researchu nadal trwa.')
            if phase == 'brief':
                cur.execute('SELECT COUNT(*) FROM research_pages WHERE research_id=%s', (research_id,))
                if cur.fetchone()[0] == 0:
                    raise HTTPException(409, 'Najpierw pobierz treści stron.')
            function = 'fetch_research_content' if phase == 'content' else 'generate_research_brief'
            job = task_queue.enqueue(f'app.worker.{function}', research_id, job_timeout=300, result_ttl=86400)
            cur.execute(f'UPDATE research_runs SET {field}=%s WHERE id=%s', (job.id, research_id))
        conn.commit()
    return {'research_id': research_id, 'status': 'queued', 'job_id': job.id}

@app.post('/research/{research_id}/fetch-content')
def start_fetch_research_content(research_id: int):
    return enqueue_research_phase(research_id, 'content')

@app.post('/research/{research_id}/brief')
def start_research_brief(research_id: int):
    return enqueue_research_phase(research_id, 'brief')

@app.post('/research/{research_id}/article')
def start_article_from_research(research_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM research_runs WHERE id=%s FOR UPDATE', (research_id,))
            if not cur.fetchone():
                raise HTTPException(404, 'Research not found')
            cur.execute('SELECT r.project_id,r.status,r.brief,p.name,p.domain,p.language,p.audience,p.brand_style,r.brief_job_id,r.content_job_id FROM research_runs r JOIN projects p ON p.id=r.project_id WHERE r.id=%s', (research_id,))
            row = cur.fetchone()
            if row[1] != 'completed' or not isinstance(row[2], dict) or (not row[2].get('summary')):
                raise HTTPException(409, 'Najpierw wygeneruj brief.')
            for id in row[8:]:
                state = job_state(id)
                if state and state['status'] in _ACTIVE:
                    raise HTTPException(409, 'Inne zadanie researchu nadal trwa.')
            cur.execute("SELECT id FROM runs WHERE research_id=%s AND status IN ('queued','running')", (research_id,))
            if cur.fetchone():
                raise HTTPException(409, 'Artykuł z tego researchu już jest w kolejce lub trwa.')
            snapshot = dict(zip(('brief', 'project_name', 'domain', 'language', 'audience', 'brand_style'), row[2:8]))
            snapshot['research_id'] = research_id
            cur.execute("INSERT INTO runs(project_id,status,research_id,research_context) VALUES(%s,'queued',%s,%s) RETURNING id", (row[0], research_id, Jsonb(snapshot)))
            run_id = cur.fetchone()[0]
        conn.commit()
    try:
        job = task_queue.enqueue('app.worker.process_research_article', run_id, job_timeout=300, result_ttl=86400)
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute('UPDATE research_runs SET article_job_id=%s WHERE id=%s', (job.id, research_id))
    except Exception as exc:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("UPDATE runs SET status='failed',finished_at=NOW() WHERE id=%s", (run_id,))
        raise HTTPException(503, 'Could not enqueue article generation') from exc
    return {'run_id': run_id, 'research_id': research_id, 'status': 'queued', 'job_id': job.id}

def article_has_active_publish_job(article_id):
    ids = set(task_queue.get_job_ids())
    for registry_class in (StartedJobRegistry, ScheduledJobRegistry, DeferredJobRegistry):
        registry = registry_class(name=task_queue.name, connection=redis_conn)
        ids.update(registry.get_job_ids())
    for id in ids:
        try:
            job = Job.fetch(id, connection=redis_conn)
        except NoSuchJobError:
            continue
        if job.func_name == 'app.worker.publish_article' and job.args and (job.args[0] == article_id):
            status = job.get_status(refresh=True)
            if getattr(status, 'value', status) in _ACTIVE:
                return True
    return False

@app.delete('/articles/{article_id}')
def delete_article(article_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT publication_status FROM articles WHERE id=%s FOR UPDATE', (article_id,))
            article = cur.fetchone()
            if not article:
                raise HTTPException(404, 'Article not found')
            if article[0] == 'pending' or article_has_active_publish_job(article_id):
                raise HTTPException(409, 'Artykuł ma aktywne lub zaplanowane wysyłanie do WordPressa. Poczekaj na zakończenie.')
            image_filename = image_before_delete(cur, article_id)
            cur.execute('DELETE FROM articles WHERE id=%s', (article_id,))
        conn.commit()
    remove_local_image(image_filename)
    return {'deleted': True, 'article_id': article_id, 'wordpress_deleted': False}

@app.delete('/research/{research_id}')
def delete_research(research_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status,content_job_id,brief_job_id,article_job_id FROM research_runs WHERE id=%s FOR UPDATE', (research_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, 'Research not found')
            if row[0] in {'queued', 'running'}:
                raise HTTPException(409, 'Research jest w kolejce lub trwa.')
            for id in row[1:]:
                state = job_state(id)
                if state and state['status'] in _ACTIVE:
                    raise HTTPException(409, 'Zadanie powiązane z researchem nadal trwa.')
            cur.execute("SELECT id FROM runs WHERE research_id=%s AND status IN ('queued','running') LIMIT 1", (research_id,))
            if cur.fetchone():
                raise HTTPException(409, 'Trwa generowanie artykułu z tego researchu.')
            cur.execute('SELECT COUNT(*) FROM research_pages WHERE research_id=%s', (research_id,))
            page_count = cur.fetchone()[0]
            cur.execute('UPDATE runs SET research_id=NULL WHERE research_id=%s', (research_id,))
            cur.execute('DELETE FROM research_pages WHERE research_id=%s', (research_id,))
            cur.execute('DELETE FROM research_runs WHERE id=%s', (research_id,))
        conn.commit()
    return {'deleted': True, 'research_id': research_id, 'deleted_pages': page_count, 'articles_preserved': True}


app.include_router(article_images_router)
