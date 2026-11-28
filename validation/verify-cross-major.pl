use strict;
use warnings FATAL => 'all';

use File::Find qw(find);
use PostgreSQL::Test::Cluster;
use PostgreSQL::Test::Utils;
use Test::More;

my $typmods = q{
	SELECT r.relname || ':' || a.attname || ':' || a.atttypmod
	FROM pg_catalog.pg_attribute a
	JOIN pg_catalog.pg_class r ON r.oid = a.attrelid
	WHERE (r.relname, a.attname) IN (
		('people', 'parent'), ('person_t', 'parent'), ('holders', 'person')
	)
	ORDER BY r.relname
};
my $user_type_oids = q{
	SELECT t.typname || ':' || t.oid || ':' || t.typarray
	FROM pg_catalog.pg_type t
	WHERE t.oid = 'person_t'::regtype OR t.typrefbase = 'person_t'::regtype
	ORDER BY t.oid
};
my $verify_people = q{
	SELECT p.name, (DEREF(p.parent)).name FROM people p ORDER BY p.name;
};
my $verify_holder = q{
	SELECT h.id, (DEREF(h.person)).name FROM holders h;
};

sub log_restore_error
{
	my ($node) = @_;
	my $path = $node->data_dir . '/pg_upgrade_output.d';
	return unless -d $path;
	my @logs;
	find({
		no_chdir => 1,
		wanted => sub {
			push @logs, $File::Find::name
			  if -f $_ && m{(?:^|/)pg_upgrade_(?:server|internal|dump[^/]*)[.]log\z};
		},
	}, $path);
	for my $log (sort @logs)
	{
		my @lines = grep { /ERROR:|FATAL:|has different|cannot be upgraded|command was:/i }
		  split /\n/, slurp_file($log);
		diag("$log: " . join("\n", @lines[0 .. ($#lines < 7 ? $#lines : 7)])) if @lines;
	}
	return;
}

chdir $PostgreSQL::Test::Utils::tmp_check;
for my $protocol ('oracle', 'postgres')
{
	subtest "IvorySQL 5.6 upgrade through $protocol port" => sub
	{
		my $old = PostgreSQL::Test::Cluster->new("released_$protocol",
			install_path => $ENV{IVORY_OLD_PREFIX});
		my $new = PostgreSQL::Test::Cluster->new("candidate_$protocol",
			install_path => $ENV{IVORY_NEW_PREFIX});
		$old->init;
		$new->init;
		$old->start;
		like($old->safe_psql('postgres', 'SELECT version()'), qr/(?:IvorySQL.*5[.]6|PostgreSQL 18[.]6)/,
			'source is the actual released major version');

		$old->safe_psql('postgres', q{
CREATE TYPE person_t AS OBJECT (name varchar2(10), parent REF person_t);
/
CREATE TABLE people OF person_t;
CREATE TABLE holders (id integer PRIMARY KEY, person REF person_t);
INSERT INTO people (name) VALUES ('root'), ('child');
UPDATE people p SET parent =
	(SELECT REF(r) FROM people r WHERE r.name = 'root')
	WHERE p.name = 'child';
INSERT INTO holders SELECT 7, REF(p) FROM people p WHERE p.name = 'root';
}, connect_to_oraport => 1);

		my $before = $old->safe_psql('postgres', $typmods);
		note("Released cluster REF typmods: $before");
		like($before, qr/\Aholders:person:(?:0|-1)\npeople:parent:(?:0|-1)\nperson_t:parent:(?:0|-1)\z/,
			'all three source attributes are present');
		my $before_oids = $old->safe_psql('postgres', $user_type_oids);
		is(scalar(split /\n/, $before_oids), 2, 'old object and REF domain are present');
		is($old->safe_psql('postgres', $verify_people, connect_to_oraport => 1),
			"child|root\nroot|", 'source object table has a meaningful stored reference');
		is($old->safe_psql('postgres', $verify_holder, connect_to_oraport => 1),
			'7|root', 'source ordinary table has a meaningful stored reference');
		$old->stop;

		my @upgrade = (
			"$ENV{IVORY_NEW_PREFIX}/bin/pg_upgrade", '--no-sync', '--copy',
			'--old-datadir' => $old->data_dir, '--new-datadir' => $new->data_dir,
			'--old-bindir' => $old->config_data('--bindir'),
			'--new-bindir' => $new->config_data('--bindir'),
			'--socketdir' => $new->host,
			'--old-port' => $old->port, '--new-port' => $new->port,
			'--old-oraport' => $old->oraport, '--new-oraport' => $new->oraport
		);
		push @upgrade, '--using-ora-pg' if $protocol eq 'postgres';
		if (!command_ok(\@upgrade, 'cross-major pg_upgrade completes'))
		{
			log_restore_error($new);
			return;
		}
		$new->start;
		is($new->safe_psql('postgres', $typmods),
			"holders:person:-1\npeople:parent:-1\nperson_t:parent:-1",
			'REF type modifiers are valid in the destination');
		is($new->safe_psql('postgres', $user_type_oids), $before_oids,
			'object and REF-domain OIDs survive');
		is($new->safe_psql('postgres', $verify_people, connect_to_oraport => 1),
			"child|root\nroot|", 'object table rows and parent value survive');
		is($new->safe_psql('postgres', $verify_holder, connect_to_oraport => 1),
			'7|root', 'ordinary table reference survives');
		like($new->safe_psql('postgres', 'SELECT person FROM holders WHERE id=7;',
			connect_to_oraport => 1), qr/\S/, 'the raw REF value can be selected from an ordinary table');
		like($new->safe_psql('postgres', q{SELECT parent FROM people WHERE name='child';},
			connect_to_oraport => 1), qr/\S/, 'the raw REF value can be selected from an object table');
		$new->stop;
	};
}

done_testing();
