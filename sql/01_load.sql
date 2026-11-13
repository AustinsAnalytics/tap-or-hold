-- Load the raw downloads into DuckDB. Every later step reads from these tables.

CREATE OR REPLACE TABLE raw_statcast AS
SELECT *
FROM read_csv('data/raw/statcast/*.csv', header = true, union_by_name = true, sample_size = -1);

CREATE OR REPLACE TABLE pbp      AS SELECT * FROM 'data/interim/pbp_pitches.parquet';
CREATE OR REPLACE TABLE games    AS SELECT * FROM 'data/interim/games.parquet';
CREATE OR REPLACE TABLE players  AS SELECT * FROM 'data/interim/players.parquet';
CREATE OR REPLACE TABLE teams    AS SELECT * FROM 'data/interim/teams.parquet';
