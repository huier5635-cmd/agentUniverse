pg_bigm_sources = files(
  'bigm_gin.c',
  'bigm_op.c',
)

if host_system == 'windows'
  pg_bigm_sources += rc_lib_gen.process(win32ver_rc, extra_args: [
    '--NAME', 'pg_bigm',
    '--FILEDESC', 'pg_bigm - bigram matching',])
endif

pg_bigm = shared_module('pg_bigm',
  pg_bigm_sources,
  kwargs: contrib_mod_args,
)
contrib_targets += pg_bigm

install_data(
  'pg_bigm.control',
  'pg_bigm--1.2.sql',
  'pg_bigm--1.1--1.2.sql',
  'pg_bigm--1.0--1.1.sql',
  kwargs: contrib_data_args,
)
