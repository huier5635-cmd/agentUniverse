use strict;
use warnings FATAL => 'all';

use File::Find qw(find);
use PostgreSQL::Test::Cluster;
use PostgreSQL::Test::Utils;
use Test::More;

my $mode = $ENV{IVORY_REF_UPGRADE_MODE};
die "unexpected validation mode" unless $mode eq 'baseline' || $mode eq 'combined';
my $old = PostgreSQL::Test::Cluster->new('old_ref',
	install_path => $ENV{IVORY_OLD_PREFIX});
my $new = PostgreSQL::Test::Cluster->new('new_ref',
	install_path => $ENV{IVORY_NEW_PREFIX});
$old->init;
$new->init;
$old->start;

$old->safe_psql('postgres', q{
CREATE TYPE person_t AS OBJECT (name varchar2(10), parent REF person_t);
/
CREATE TABLE people OF person_t;
INSERT INTO people (name) VALUES ('root'), ('child');
UPDATE people p SET parent =
	(SELECT REF(r) FROM people r WHERE r.name = 'root')
	WHERE p.name = 'child';
}, connect_to_oraport => 1);

my $typmod = q{
	SELECT a.atttypmod
	FROM pg_catalog.pg_attribute a
	WHERE a.attname = 'parent' AND a.attrelid IN (
		'people'::regclass,
		(SELECT typrelid FROM pg_catalog.pg_type WHERE oid = 'person_t'::regtype)
	)
	ORDER BY a.attrelid
};
my $type_oids = q{
	SELECT t.oid, t.typarray
	FROM pg_catalog.pg_type t
	WHERE t.oid = 'person_t'::regtype OR t.typrefbase = 'person_t'::regtype
	ORDER BY t.oid
};
is($old->safe_psql('postgres', $typmod), "0\n0", 'legacy REF typmods');
my $before = $old->safe_psql('postgres', $type_oids);
is(scalar(split /\n/, $before), 2, 'old object and REF domain exist');
$old->stop;

chdir $PostgreSQL::Test::Utils::tmp_check;
my @upgrade = (
	"$ENV{IVORY_NEW_PREFIX}/bin/pg_upgrade", '--no-sync', '--copy',
	'--old-datadir' => $old->data_dir,
	'--new-datadir' => $new->data_dir,
	'--old-bindir' => $old->config_data('--bindir'),
	'--new-bindir' => $new->config_data('--bindir'),
	'--socketdir' => $new->host,
	'--old-port' => $old->port,
	'--new-port' => $new->port,
	'--old-oraport' => $old->oraport,
	'--new-oraport' => $new->oraport
);

if ($mode eq 'baseline')
{
	command_fails(\@upgrade, 'upgrade with #3270 only rejects the REF mismatch');
	my @logs;
	find({
		no_chdir => 1,
		wanted => sub {
			push @logs, $File::Find::name
			  if -f $_ && m{(?:^|/)pg_upgrade_dump[^/]*[.]log\z};
		}
	}, $new->data_dir . '/pg_upgrade_output.d');
	like(join("\n", map { slurp_file($_) } @logs),
		qr/has different type for column "parent"/,
		'upgrade stops specifically on the parent REF type');
}
else
{
	command_ok(\@upgrade, 'upgrade with #3270 and #3283 through Oracle port');
	$new->start;
	is($new->safe_psql('postgres', $typmod), "-1\n-1",
		'upgrade normalizes both REF typmods');
	is($new->safe_psql('postgres', $type_oids), $before,
		'object and REF domain OIDs survive');
	is($new->safe_psql('postgres', q{
		SELECT p.name, (DEREF(p.parent)).name
		FROM people p ORDER BY p.name
	}, connect_to_oraport => 1),
		"child|root\nroot|", 'rows and stored parent reference survive');
	$new->stop;
}

done_testing();
