-- Asset filenames are rarely sentences (opaque hex/numeric IDs, or human names joined with
-- underscores/hyphens, e.g. "sourdough_starter.jpg"). Postgres's default parser treats such a
-- filename as one opaque token, so a plain to_tsvector would never match "sourdough" alone.
-- Normalizing separators to spaces first makes filename search actually useful.
CREATE FUNCTION filename_search_text(value text) RETURNS text AS $$
    SELECT replace(replace(replace(value, '_', ' '), '-', ' '), '.', ' ');
$$ LANGUAGE sql IMMUTABLE;

ALTER TABLE conversations ADD COLUMN search_vector tsvector;
ALTER TABLE events ADD COLUMN search_vector tsvector;
ALTER TABLE records ADD COLUMN search_vector tsvector;
ALTER TABLE assets ADD COLUMN search_vector tsvector;

UPDATE conversations SET search_vector =
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', left(metadata::text, 10000)), 'D');

UPDATE events SET search_vector =
    setweight(to_tsvector('english', coalesce(text, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(actor_identity, '')), 'B') ||
    setweight(to_tsvector('english', event_type), 'C') ||
    setweight(to_tsvector('english', left(metadata::text, 10000)), 'D');

UPDATE records SET search_vector =
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(text, '')), 'B') ||
    setweight(to_tsvector('english', record_type), 'C') ||
    setweight(to_tsvector('english', left(metadata::text, 10000)), 'D');

UPDATE assets SET search_vector =
    setweight(to_tsvector('english', filename_search_text(filename)), 'A') ||
    setweight(to_tsvector('english', coalesce(mime_type, '')), 'C') ||
    setweight(to_tsvector('english', left(metadata::text, 10000)), 'D');

ALTER TABLE conversations ALTER COLUMN search_vector SET NOT NULL;
ALTER TABLE events ALTER COLUMN search_vector SET NOT NULL;
ALTER TABLE records ALTER COLUMN search_vector SET NOT NULL;
ALTER TABLE assets ALTER COLUMN search_vector SET NOT NULL;

CREATE INDEX conversations_search_vector_idx ON conversations USING GIN (search_vector);
CREATE INDEX events_search_vector_idx ON events USING GIN (search_vector);
CREATE INDEX records_search_vector_idx ON records USING GIN (search_vector);
CREATE INDEX assets_search_vector_idx ON assets USING GIN (search_vector);

CREATE FUNCTION conversations_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', left(NEW.metadata::text, 10000)), 'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER conversations_search_vector_trigger
    BEFORE INSERT OR UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION conversations_search_vector_update();

CREATE FUNCTION events_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.text, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.actor_identity, '')), 'B') ||
        setweight(to_tsvector('english', NEW.event_type), 'C') ||
        setweight(to_tsvector('english', left(NEW.metadata::text, 10000)), 'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_search_vector_trigger
    BEFORE INSERT OR UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION events_search_vector_update();

CREATE FUNCTION records_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.text, '')), 'B') ||
        setweight(to_tsvector('english', NEW.record_type), 'C') ||
        setweight(to_tsvector('english', left(NEW.metadata::text, 10000)), 'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER records_search_vector_trigger
    BEFORE INSERT OR UPDATE ON records
    FOR EACH ROW EXECUTE FUNCTION records_search_vector_update();

CREATE FUNCTION assets_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', filename_search_text(NEW.filename)), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.mime_type, '')), 'C') ||
        setweight(to_tsvector('english', left(NEW.metadata::text, 10000)), 'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER assets_search_vector_trigger
    BEFORE INSERT OR UPDATE ON assets
    FOR EACH ROW EXECUTE FUNCTION assets_search_vector_update();
