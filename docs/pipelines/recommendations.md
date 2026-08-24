# Recommendations and automation

## Interest profile

Rebuilt incrementally by a nightly job from `user_events`. Event weights come from the
original plan and decay exponentially with a ninety-day half-life, so a profile
reflects current interest rather than the first weeks of use.

```
request +10   favourite +8   completed +5   rewatched +4
>50% watched +3   clicked +1   abandoned early -2
not interested -8   blocked -100
```

Each axis — tags, performers, studios, quality — is normalized to 0–1.

## Scoring

Weights live in configuration, not in code, so they can be tuned without a deployment.

```
0.30 tag match + 0.25 performer match + 0.15 studio match
+ 0.10 quality preference + 0.10 recency + 0.10 general popularity
+ 0.10 household rating
- hard blocks
```

## Feed switches

Four administrator switches change what generation produces, on top of the weights.
`recommendation_use_ratings` decides whether the household's stars reach the rating
component at all. `recommendation_hide_finished` drops titles the user has completed,
read from playback rather than the event stream because events are pruned on a
retention schedule. `recommendation_include_friend_picks` and
`recommendation_include_shorts` decide whether titles someone sent the user, and
titles shorter than a clip's maximum length, are eligible candidates.

## Ranking

Raw score is not the final order. At most two entries per performer and three per
studio appear in the top twenty, and twenty percent of slots are reserved for
exploration. Without this the list collapses onto one performer and stops being
useful.

## Explanations

Every candidate stores a structured reason: matched tags, matched performers, the
dominant factor and the full score breakdown. The sentence is composed in the
frontend so it can be translated. No user-facing prose is generated on the server.

## Automatic downloads

Off by default. A candidate is only acquired automatically when all of these hold:

- automation enabled for that user
- score above the configured threshold
- no blocked tag
- not a duplicate
- daily size budget not exhausted
- at least fifteen percent free disk
- quality within the profile
- metadata confidence sufficient

Defaults: 10 GB per user per day, two concurrent automatic jobs, three automatic
downloads per day. Manual requests always preempt automatic ones. Every automatic
decision is written to the audit log with its score breakdown, so it can be explained
after the fact.
