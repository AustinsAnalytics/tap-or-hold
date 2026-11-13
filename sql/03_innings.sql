-- Runs scored from each pitch to the end of its half-inning, and which half-innings were played to three outs.
-- A half-inning is cut short only when it is the last one of the game and the home team won in it (a walk-off),
-- or when a game was stopped early. Those are excluded from run expectancy.

CREATE OR REPLACE TABLE half_innings AS
WITH h AS (
    SELECT game_pk, inning, is_top,
           min(bat_score) AS start_score,
           max(post_bat_score) AS end_score,
           count(*) AS pitches
    FROM pitches
    GROUP BY ALL
),
last_half AS (
    SELECT game_pk, max(inning * 2 + (NOT is_top)::INT) AS last_key FROM pitches GROUP BY ALL
)
SELECT h.*, h.end_score - h.start_score AS runs,
       (h.inning * 2 + (NOT h.is_top)::INT) = l.last_key AS is_last_half,
       NOT (
           ((h.inning * 2 + (NOT h.is_top)::INT) = l.last_key AND NOT h.is_top AND g.home_runs > g.away_runs)
           OR ((h.inning * 2 + (NOT h.is_top)::INT) = l.last_key AND g.detailed_state <> 'Final')
       ) AS complete
FROM h
JOIN last_half l USING (game_pk)
JOIN games g USING (game_pk);

CREATE OR REPLACE TABLE pitch_runs AS
SELECT p.game_pk, p.at_bat_number, p.pitch_number,
       h.end_score - p.bat_score AS runs_rest,
       h.complete
FROM pitches p
JOIN half_innings h USING (game_pk, inning, is_top);
