# Changelog

## 2026-09-09

- Full Teacher Logbook migration support; no common, core, shared authentication or database modules modified.
- Added explicit legacy JSON migration with dry-run validation, new student IDs, safe relation mapping, empty-class checks and transactional writes.
- Fixed date and keyword filters, stable pagination, referenced committee-role deletion checks and update validation errors.
- Seat swaps release both active unique positions before reassignment; cleared boards reuse their existing row; restored board versions remain monotonic.
- Backup validation now rejects malformed fields, duplicate IDs, unsafe ownership fields, invalid course times, broken references and invalid seat layouts before replacement. Restore errors roll back the transaction; course times serialize and restore correctly.
- Added version-controlled tests under apps/teacher_logbook/tests with a module-local ignore exception because the root rules ignore test paths. No root ignore rules changed. The local legacy test fixture was updated for the stricter backup validation.
- Verification: 43 isolated schema, service and HTTP contract tests pass. CRUD response tests cover all 18 generic resources and both PATCH/POST update methods. No live account or production business data was created or deleted.
- Teacher Logbook: typed resource, dashboard, seat layout and backup validation responses.
- Added POST update aliases within the Teacher Logbook router for miniapp compatibility; existing PATCH endpoints remain available.
- Student CSV exports query all authorized students, not only the first 100.
- Dashboard queries actual high-risk alerts, pending todos, latest exam and recent work records.
- Changes are confined to the Teacher Logbook module, its tests and this log. No common or core files modified.
