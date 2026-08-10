# GitLab Outage — 2017-01-31 (Accidental primary database deletion)

Incident date: 2017-01-31
Source: GitLab public postmortem (https://about.gitlab.com/blog/postmortem-of-database-outage-of-january-31/).

Published on: February 10, 2017
20 min read

## Postmortem of database outage of January 31
Postmortem on the database outage of January 31 2017 with the lessons we learned.
On January 31st 2017, we experienced a major service outage for one of our products, the online service GitLab.com. The outage was caused by an accidental removal of data from our primary database server.
This incident caused the GitLab.com service to be unavailable for many hours. We also lost some production data that we were eventually unable to recover. Specifically, we lost modifications to database data such as projects, comments, user accounts, issues and snippets, that took place between 17:20 and 00:00 UTC on January 31. Our best estimate is that it affected roughly 5,000 projects, 5,000 c…
Losing production data is unacceptable. To ensure this does not happen again we're working on multiple improvements to our operations & recovery procedures for GitLab.com. In this article we'll look at what went wrong, what we did to recover, and what we'll do to prevent this from happening in the future.
To the GitLab.com users whose data we lost and to the people affected by the outage: we're sorry. I apologize personally, as GitLab's CEO, and on behalf of everyone at GitLab.

## Database setup
GitLab.com currently uses a single primary and a single secondary in hot-standby
mode. The standby is only used for failover purposes. In this setup a single
database has to handle all the load, which is not ideal. The primary's hostname
is db1.cluster.gitlab.com , while the secondary's hostname is db2.cluster.gitlab.com .
In the past we've had various other issues with this particular setup due to db1.cluster.gitlab.com being a single point of failure. For example:
- A database outage on November 28th, 2016 due to project_authorizations having too much bloat
- CI distributed heavy polling and exclusive row locking for seconds takes GitLab.com down
- Scary DB spikes

## Timeline
On January 31st an engineer started setting up multiple PostgreSQL servers in
our staging environment. The plan was to try out pgpool-II to see if it
would reduce the load on our database by load balancing queries between the
available hosts. Here is the issue for that plan: infrastructure#259 .
± 17:20 UTC: prior to starting this work, our engineer took an LVM snapshot
of the production database and loaded this into the staging environment. This was
necessary to ensure the staging database was up to date, allowing for more
accurate load testing. This procedure normally happens automatically once every
24 hours (at 01:00 UTC), but they wanted a more up to date copy of the
database.
± 19:00 UTC: GitLab.com starts experiencing an increase in database load due
to what we suspect was spam. In the week leading up to this event GitLab.com had
been experiencing similar problems, but not this severe. One of the problems
this load caused was that many users were not able to post comments on issues
and merge requests. Getting the load under control took several hours.
We would later find out that part of the load was caused by a background job
trying to remove a GitLab employee and their associated data. This was the
result of their account being flagged for abuse and accidentally scheduled for removal. More information regarding this particular problem can be found in the
issue "Removal of users by spam should not hard
delete" .
± 23:00 UTC: Due to the increased load, our PostgreSQL secondary's
replication process started to lag behind. The replication failed as WAL
segments needed by the secondary were already removed from the primary. As
GitLab.com was not using WAL archiving, the secondary had to be re-synchronised
manually. This involves removing the
existing data directory on the secondary, and running pg_basebackup …
One of the engineers went to the secondary and wiped the data directory, then
ran pg_basebackup . Unfortunately pg_basebackup would hang, producing no
meaningful output, despite the --verbose option being set. After a few tries pg_basebackup mentioned that it could not connect due to the master not having
enough available replication connections (as controlled by the max_wal_senders option).
To resolve this our engineers decided to temporarily increase max_wal_senders from the default value of 3 to 32 . When applying the
settings, PostgreSQL refused to restart, claiming too many semaphores were being
created. This can happen when, for example, max_connections is set too high. In
our case this was set to 8000 . Such a value is way too high, yet it had been
applied almost a year ago and…
Unfortunately this did not resolve the problem of pg_basebackup not starting
replication immediately. One of the engineers decided to run it with strace to
see what it was blocking on. strace showed that pg_basebackup was hanging in
a poll call, but that did not provide any other meaningful information that might
have explained why.
± 23:30 UTC: one of the engineers thinks that perhaps pg_basebackup created some files in the PostgreSQL data directory of the secondary during the
previous attempts to run it. While normally pg_basebackup prints an error when
this is the case, the engineer in question wasn't too sure what was going on. It
would later be revealed by another engineer (who wasn't around at the time) that
this is nor…
Trying to restore the replication process, an engineer proceeds to wipe the
PostgreSQL database directory, errantly thinking they were doing so on the
secondary. Unfortunately this process was executed on the primary instead. The
engineer terminated the process a second or two after noticing their mistake,
but at this point around 300 GB of data had already been removed.
Hoping they could restore the database the engineers involved went to look for
the database backups, and asked for help on Slack. Unfortunately the process of
both finding and using backups failed completely.

## Broken recovery procedures
This brings us to the recovery procedures. Normally in an event like this, one
should be able to restore a database in relatively little time using a recent
backup, though some form of data loss can not always be prevented. For
GitLab.com we have the following procedures in place:
- Every 24 hours a backup is generated using pg_dump , this backup is uploaded
to Amazon S3. Old backups are automatically removed after some time.
- Every 24 hours we generate an LVM snapshot of the disk storing the production
database data. This snapshot is then loaded into the staging environment,
allowing us to more safely test changes without impacting our production
environment. Direct access to the staging database is restricted, similar to
our production database.
- For various servers (e.g. the NFS servers storing Git data) we use Azure disk
snapshots. These snapshots are taken once per 24 hours.
- Replication between PostgreSQL hosts, primarily used for failover purposes
and not for disaster recovery.
At this point the replication process was broken and data had already been wiped
from both the primary and secondary, meaning we could not restore from either
host.

## Database backups using pg_dump
When we went to look for the pg_dump backups we found out they were not there.
The S3 bucket was empty, and there was no recent backup to be found anywhere.
Upon closer inspection we found out that the backup procedure was using pg_dump 9.2, while our database is running PostgreSQL 9.6 (for Postgres, 9.x
releases are considered major). A difference in major versions results in pg_dump producing an…
The difference is the result of how our Omnibus package works. We currently
support both PostgreSQL 9.2 and 9.6, allowing users to upgrade (either manually
or using commands provided by the package). To determine the correct version to
use the Omnibus package looks at the PostgreSQL version of the database cluster
(as determined by $PGDIR/PG_VERSION , with $PGDIR being the path to the data
directo…
The pg_dump procedure was executed on a regular application server, not the
database server. As a result there is no PostgreSQL data directory present on
these servers, thus Omnibus defaults to PostgreSQL 9.2. This in turn resulted in pg_dump terminating with an error.

## Root causes
(see analysis section above)


## Follow-ups
(see mitigation section above)
