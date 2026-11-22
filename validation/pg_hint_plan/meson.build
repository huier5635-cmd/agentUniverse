pg_hint_plan_sources = files('pg_hint_plan.c')

pg_hint_plan_scan = custom_target('pg_hint_plan_scan',
  input: 'query_scan.l',
  output: 'query_scan.c',
  command: flex_cmd,
)
generated_sources += pg_hint_plan_scan
pg_hint_plan_sources += pg_hint_plan_scan

if host_system == 'windows'
  pg_hint_plan_sources += rc_lib_gen.process(win32ver_rc, extra_args: [
    '--NAME', 'pg_hint_plan',
    '--FILEDESC', 'pg_hint_plan - optimizer hints',])
endif

pg_hint_plan = shared_module('pg_hint_plan',
  pg_hint_plan_sources,
  include_directories: include_directories('.'),
  kwargs: contrib_mod_args,
)
contrib_targets += pg_hint_plan

install_data(
  'pg_hint_plan.control',
  'pg_hint_plan--1.3.0.sql',
  'pg_hint_plan--1.3.0--1.3.1.sql',
  'pg_hint_plan--1.3.1--1.3.2.sql',
  'pg_hint_plan--1.3.2--1.3.3.sql',
  'pg_hint_plan--1.3.3--1.3.4.sql',
  'pg_hint_plan--1.3.4--1.3.5.sql',
  'pg_hint_plan--1.3.5--1.3.6.sql',
  'pg_hint_plan--1.3.6--1.3.7.sql',
  'pg_hint_plan--1.3.7--1.3.8.sql',
  'pg_hint_plan--1.3.8--1.3.9.sql',
  'pg_hint_plan--1.3.9--1.3.10.sql',
  'pg_hint_plan--1.3.10--1.3.11.sql',
  'pg_hint_plan--1.3.11--1.4.sql',
  'pg_hint_plan--1.4--1.4.1.sql',
  'pg_hint_plan--1.4.1--1.4.2.sql',
  'pg_hint_plan--1.4.2--1.4.3.sql',
  'pg_hint_plan--1.4.3--1.4.4.sql',
  'pg_hint_plan--1.4.4--1.4.5.sql',
  'pg_hint_plan--1.4.5--1.5.sql',
  'pg_hint_plan--1.5--1.5.1.sql',
  'pg_hint_plan--1.5.1--1.5.2.sql',
  'pg_hint_plan--1.5.2--1.5.3.sql',
  'pg_hint_plan--1.5.3--1.5.4.sql',
  'pg_hint_plan--1.5.4--1.6.0.sql',
  'pg_hint_plan--1.6.0--1.6.1.sql',
  'pg_hint_plan--1.6.1--1.6.2.sql',
  'pg_hint_plan--1.6.2--1.6.3.sql',
  'pg_hint_plan--1.6.3--1.7.0.sql',
  'pg_hint_plan--1.7.0--1.7.1.sql',
  'pg_hint_plan--1.7.1--1.7.2.sql',
  'pg_hint_plan--1.7.2--1.8.0.sql',
  'pg_hint_plan--1.8.0--1.8.1.sql',
  'pg_hint_plan--1.8.1--1.9.0.sql',
  kwargs: contrib_data_args,
)
