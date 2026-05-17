-- ============================================================
-- Migration 001 — Schéma initial cours-generator (Supabase)
-- ============================================================
-- À exécuter dans : Supabase Dashboard → SQL Editor
-- Après exécution, activer RLS et créer les policies selon besoins.
-- ============================================================


-- ── Extensions ───────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- ── Profiles (extension de auth.users géré par Supabase Auth) ────────────────
CREATE TABLE IF NOT EXISTS profiles (
    id              UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name    TEXT,
    avatar_url      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE profiles IS 'Profil utilisateur public, lié à auth.users.';


-- ── Projects ─────────────────────────────────────────────────────────────────
-- Un project = un cours (ou une collection de cours liés).
CREATE TABLE IF NOT EXISTS projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES profiles(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, slug)
);

COMMENT ON TABLE projects IS 'Projet pédagogique. Un utilisateur peut en avoir plusieurs.';
CREATE INDEX ON projects(user_id);


-- ── Source documents ─────────────────────────────────────────────────────────
-- PDF sources uploadés par l'utilisateur.
CREATE TABLE IF NOT EXISTS source_documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES profiles(id),
    filename        TEXT NOT NULL,
    storage_path    TEXT NOT NULL,    -- chemin dans Supabase Storage bucket "sources"
    mime_type       TEXT DEFAULT 'application/pdf',
    size_bytes      BIGINT,
    page_count      INT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE source_documents IS 'Fichiers PDF sources uploadés.';
CREATE INDEX ON source_documents(project_id);


-- ── Generation jobs ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS generation_jobs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE SET NULL,
    user_id             UUID REFERENCES profiles(id),
    source_document_id  UUID REFERENCES source_documents(id) ON DELETE SET NULL,

    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','done','error')),
    step        TEXT,                   -- étape courante (ex: "structure", "render")
    config      JSONB DEFAULT '{}',     -- {skip_audio: bool, matiere: str, ...}
    error       TEXT,                   -- message d'erreur si status='error'

    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE generation_jobs IS 'Suivi des jobs de génération asynchrones.';
CREATE INDEX ON generation_jobs(user_id, created_at DESC);
CREATE INDEX ON generation_jobs(status);


-- ── Course versions ───────────────────────────────────────────────────────────
-- Chaque génération produit une version du CourseModel JSON.
-- Plusieurs versions peuvent exister pour un même project (historique).
CREATE TABLE IF NOT EXISTS course_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES generation_jobs(id) ON DELETE SET NULL,
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES profiles(id),

    version_number  INT NOT NULL DEFAULT 1,
    course_model    JSONB NOT NULL,     -- CourseModel sérialisé (sections, formulas, quiz...)
    is_current      BOOLEAN DEFAULT TRUE,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, version_number)
);

COMMENT ON TABLE course_versions IS
    'Versions du CourseModel JSON. Découplé du HTML final → permet re-rendu sans LLM.';
CREATE INDEX ON course_versions(project_id, is_current);


-- ── Published sites ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS published_sites (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
    course_version_id   UUID REFERENCES course_versions(id) ON DELETE SET NULL,
    user_id             UUID REFERENCES profiles(id),

    -- URL publique (GitHub Pages, Vercel, ou Supabase Storage public URL)
    published_url       TEXT,
    -- Optionnel : stocker le HTML rendu si pas de CDN
    storage_path        TEXT,   -- chemin dans Supabase Storage bucket "sites"

    published_at        TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE published_sites IS 'Sites publiés (publication découplée de la génération).';
CREATE INDEX ON published_sites(project_id);


-- ── Assets ────────────────────────────────────────────────────────────────────
-- Fichiers médias générés (slides PDF, audio, etc.)
CREATE TABLE IF NOT EXISTS assets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES generation_jobs(id) ON DELETE SET NULL,
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES profiles(id),

    asset_type      TEXT NOT NULL,
                    -- 'source_pdf' | 'slides_pdf' | 'slide_page' | 'audio' | 'html'
    filename        TEXT NOT NULL,
    storage_path    TEXT NOT NULL,  -- Supabase Storage: bucket "assets"
    public_url      TEXT,
    size_bytes      BIGINT,
    metadata        JSONB DEFAULT '{}',  -- {page_number: int, slide_count: int, ...}

    created_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE assets IS 'Médias générés : slides, audio, PDFs.';
CREATE INDEX ON assets(job_id);
CREATE INDEX ON assets(project_id, asset_type);


-- ── Triggers: updated_at automatique ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_projects_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_profiles_updated_at
    BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── Row Level Security (à activer après tests) ────────────────────────────────
-- ALTER TABLE projects         ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE source_documents ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE generation_jobs  ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE course_versions  ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE published_sites  ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE assets           ENABLE ROW LEVEL SECURITY;

-- Exemple de policy (un user ne voit que ses propres projets) :
-- CREATE POLICY "own projects" ON projects
--     FOR ALL USING (user_id = auth.uid());
