# Indexer response recordings

These are the smallest credential-redacted excerpts that preserve the original
RSS items and protocol attributes. They are intentionally static: tests must
never query an indexer or require an account.

| Fixture | Protocol | Source |
| --- | --- | --- |
| `torznab-anime-tosho.xml` | Torznab | [Prowlarr's AnimeTosho response](https://github.com/Prowlarr/Prowlarr/blob/1efad4bbc390e1ee20818ea64748f7f9f24d2a13/src/NzbDrone.Core.Test/Files/Indexers/Torznab/torznab_animetosho.xml), blob `94505a443b96d48ae2039d434b33d500bfaaa035` |
| `torznab-hdaccess.xml` | Torznab | [Prowlarr's HDAccess response](https://github.com/Prowlarr/Prowlarr/blob/1efad4bbc390e1ee20818ea64748f7f9f24d2a13/src/NzbDrone.Core.Test/Files/Indexers/Torznab/torznab_hdaccess_net.xml), blob `98b284bf5a5341010d7d8277277edcfcea201d47` |
| `newznab-nzbsu.xml` | Newznab | [Prowlarr's NZB.su response](https://github.com/Prowlarr/Prowlarr/blob/1efad4bbc390e1ee20818ea64748f7f9f24d2a13/src/NzbDrone.Core.Test/Files/Indexers/Newznab/newznab_nzb_su.xml), blob `ea68ee154752b0675482e7687c5426320200ae35` |
| `newznab-drunkenslug.xml` | Newznab | Credential-redacted item from the [public Sonarr DrunkenSlug API response](https://forums.sonarr.tv/t/drunkenslug-api-returning-sd-shows-quality-as-unknown/21069) |

`malformed.xml`, `partial.xml`, and `empty.xml` are deliberate negative cases,
not recordings.

## Refreshing a fixture

1. Query a provider's documented `t=search` endpoint with a disposable test
   account: `curl --get "$BASE_URL/api" --data-urlencode "apikey=$API_KEY"
   --data-urlencode 't=search' --data-urlencode 'q=fixture probe'`.
2. Keep only representative `<item>` elements and the namespace declaration.
   Replace every API key, passkey, token, cookie-derived query value, and private
   hostname with `redacted`; retain attribute names, values, and XML shape.
3. Add the provider, immutable source URL or capture date, and sanitisation notes
   to the table above. Update assertions rather than weakening them.
