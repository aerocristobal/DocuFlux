# Table-Heavy Sample (Story 1.4)

Round-trip fixture for `shared/table_postprocess.py`. Mixes well-formed
tables with the ragged shapes real Marker/Pandoc output produces, plus one
genuinely unrepairable table, so tests can exercise `normalize_tables()`
against something closer to a real converted document than the small
per-case fixtures in `test_table_postprocess.py`.

## Well-formed table

| Name | Age | City |
| --- | --- | --- |
| Alice | 30 | Boston |
| Bob | 25 | Seattle |

Some prose between tables, to make sure the scanner correctly resumes
line-by-line processing after a table block ends.

## Short header row

| Name |
| --- | --- |
| Alice | 30 |
| Bob | 25 |

## Merged-cell body row (pipe inside a cell became a false column break)

| Name | Notes |
| --- | --- |
| Alice | 30 | likes | hiking |
| Bob | 25 |

## Short body row

| Name | Age | City |
| --- | --- | --- |
| Alice | 30 |

## Alignment markers

| Left | Center | Right |
| :--- | :---: | ---: |
| a | b | c |

## Unrepairable garbage table

|  |  |
| --- | --- | --- |
| x | y |

More trailing prose after the last table, unrelated to any table content.
