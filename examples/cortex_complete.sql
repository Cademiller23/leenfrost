-- Leenfrost → Snowflake Cortex
-- Payload is ALREADY density-pruned by the gate (~32% fewer tokens).
-- Run in Snowsight while logged in as ACCOUNTADMIN.

USE WAREHOUSE LEENFROST_WH;
USE DATABASE LEENFROST;

-- Probe
SELECT SNOWFLAKE.CORTEX.COMPLETE(
  'llama3.1-70b',
  'Reply with exactly: CORTEX_OK'
) AS probe;

-- Full gated cyber SOC prompt (regenerate anytime via: python examples/cortex_run.py)
-- Paste the $$...$$ block printed by cortex_run.py below this comment when demoing.
