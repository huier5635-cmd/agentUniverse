CREATE EXTENSION pg_hint_plan;
SELECT 'version=' || extversion
FROM pg_catalog.pg_extension
WHERE extname = 'pg_hint_plan';

CREATE TABLE hint_target (id int PRIMARY KEY, body text);
INSERT INTO hint_target
SELECT id, 'row-' || id FROM generate_series(1, 10000) AS id;
ANALYZE hint_target;
SET enable_seqscan = off;
SET enable_bitmapscan = off;
SET enable_indexonlyscan = off;

SELECT 'baseline-plan';
EXPLAIN (COSTS OFF) SELECT body FROM hint_target WHERE id = 42;

LOAD 'pg_hint_plan';
SELECT 'hinted-plan';
/*+ SeqScan(hint_target) */
EXPLAIN (COSTS OFF) SELECT body FROM hint_target WHERE id = 42;

/*+ SeqScan(hint_target) */
SELECT 'result=' || body FROM hint_target WHERE id = 42;
