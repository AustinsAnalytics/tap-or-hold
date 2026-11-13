-- One clean row per regular-season pitch: game state, teams, the call, the challenge, and where the pitch
-- crossed the plane ABS actually judges (the middle of home plate, 8.5 in behind the front edge).

CREATE OR REPLACE TABLE pitches AS
WITH s AS (
    SELECT *,
           CASE WHEN inning_topbot = 'Top' THEN away_team ELSE home_team END AS bat_abbr,
           CASE WHEN inning_topbot = 'Top' THEN home_team ELSE away_team END AS fld_abbr
    FROM raw_statcast
    WHERE game_type = 'R'
    -- Savant occasionally repeats a pitch across day files; keep one copy of each
    QUALIFY row_number() OVER (PARTITION BY game_pk, at_bat_number, pitch_number ORDER BY sv_id NULLS LAST) = 1
),
geo AS (
    SELECT *,
           -- time (s) from the y = 50 ft reference plane to the front of the plate (y = 17/12 ft)
           (-vy0 - sqrt(vy0 * vy0 - 2 * ay * (50 - 17.0 / 12))) / ay AS t_front
    FROM s
),
geo2 AS (
    SELECT *,
           vx0 + ax * t_front AS vx_f,
           vy0 + ay * t_front AS vy_f,
           vz0 + az * t_front AS vz_f
    FROM geo
),
geo3 AS (
    SELECT *,
           -- extra time to travel the 8.5 in from the front edge to the middle of the plate
           (-vy_f - sqrt(vy_f * vy_f - 2 * ay * (8.5 / 12))) / ay AS dt_mid
    FROM geo2
),
mid AS (
    SELECT *,
           plate_x + vx_f * dt_mid + 0.5 * ax * dt_mid * dt_mid AS x_mid,
           plate_z + vz_f * dt_mid + 0.5 * az * dt_mid * dt_mid AS z_mid
    FROM geo3
)
SELECT
    m.game_pk, m.game_date, m.at_bat_number, m.pitch_number,
    m.inning, m.inning_topbot = 'Top' AS is_top,
    m.outs_when_up AS outs, m.balls, m.strikes,
    (m.on_1b IS NOT NULL)::INT + 2 * (m.on_2b IS NOT NULL)::INT + 4 * (m.on_3b IS NOT NULL)::INT AS base_state,
    m.home_score, m.away_score, m.bat_score, m.fld_score, m.post_bat_score, m.post_home_score, m.post_away_score,
    m.home_team, m.away_team, m.bat_abbr, m.fld_abbr,
    tb.team_id AS bat_team_id, tf.team_id AS fld_team_id,
    m.batter, m.pitcher, m.fielder_2 AS catcher, m.stand, m.p_throws,
    m.description, m.events, m.type AS pitch_result_type, m.pitch_type, m.release_speed, m.pfx_x, m.pfx_z,
    m.plate_x, m.plate_z, m.sz_top, m.sz_bot, m.x_mid, m.z_mid,
    m.home_win_exp, m.bat_win_exp, m.delta_run_exp, m.delta_home_win_exp,
    -- the call as it stands after any challenge
    m.description IN ('ball', 'called_strike', 'blocked_ball', 'pitchout') AS is_called,
    CASE WHEN m.description = 'called_strike' THEN 'S'
         WHEN m.description IN ('ball', 'blocked_ball', 'pitchout') THEN 'B' END AS final_call,
    coalesce(p.review_type = 'MJ', false) AS challenged,
    CASE WHEN p.review_type = 'MJ' THEN p.overturned END AS overturned,
    CASE WHEN p.review_type = 'MJ' THEN p.challenge_team_id END AS challenge_team_id,
    CASE WHEN p.review_type = 'MJ' THEN p.challenger_id END AS challenger_id,
    p.game_pk IS NOT NULL AS matched_api
FROM mid m
LEFT JOIN teams tb ON tb.abbr = m.bat_abbr
LEFT JOIN teams tf ON tf.abbr = m.fld_abbr
LEFT JOIN pbp p USING (game_pk, at_bat_number, pitch_number);

-- The umpire's original call: flip the final call back when a challenge overturned it.
ALTER TABLE pitches ADD COLUMN orig_call VARCHAR;
UPDATE pitches
SET orig_call = CASE WHEN challenged AND overturned THEN (CASE final_call WHEN 'S' THEN 'B' ELSE 'S' END)
                     ELSE final_call END;

-- Distance (inches) from the ball's centre to the edge of the batter's ABS rectangle, measured in the plane
-- ABS uses (mid-plate) and, for comparison, at the front of the plate. Positive = outside, negative = inside.
CREATE OR REPLACE MACRO edge_in(x, z, top, bot) AS
    12 * CASE
        WHEN abs(x) - 8.5 / 12 <= 0 AND z - top <= 0 AND bot - z <= 0
            THEN greatest(abs(x) - 8.5 / 12, z - top, bot - z)
        ELSE sqrt(power(greatest(abs(x) - 8.5 / 12, 0), 2) + power(greatest(z - top, bot - z, 0), 2))
    END;

ALTER TABLE pitches ADD COLUMN edge_mid_in DOUBLE;
ALTER TABLE pitches ADD COLUMN edge_front_in DOUBLE;
UPDATE pitches SET edge_mid_in = edge_in(x_mid, z_mid, sz_top, sz_bot),
                   edge_front_in = edge_in(plate_x, plate_z, sz_top, sz_bot);
