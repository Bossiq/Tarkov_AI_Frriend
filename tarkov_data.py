"""
Tarkov quest and game knowledge — injected into LLM context.

This gives the AI accurate information about quests, maps, ammo, and
mechanics so it can answer specific questions correctly even with a
smaller model that doesn't have this knowledge baked in.
"""

# Key quest data that the AI needs to answer common questions.
# This gets injected into the system prompt as reference material.

QUEST_REFERENCE = '''
KEY TARKOV QUESTS (by trader):

PRAPOR:
- Debut: Kill 5 Scavs on Customs while wearing a balaclava and scav vest
- Checking: Find 2 bronze pocket watches in Customs truck (key needed: Dorm key 303)
- Shootout Picnic: Kill 15 Scavs on Woods
- BP Depot: Place markers on fuel tankers on Customs
- The Punisher series: Kill PMCs with specific weapons/armor across maps
- Grenadier: Kill 12 PMCs with grenades

THERAPIST:
- Shortage: Find and turn in 3 Salewa first aid kits
- Painkiller: Find and turn in 4 Analgin painkillers
- Supply Plans: Obtain documents case on Customs
- Health Care Privacy Part 1-5: Series involving medical items and keys
- Private Clinic: Find Ledx and hand it over

SKIER:
- Supplier: Kill 15 Scavs on Customs
- Stirrup: Kill 3 PMCs with pistols
- What's On The Flash Drive: Find 2 flash drives (filing cabinets, safes)
- Friend From The West Part 1: Kill 7 USEC PMCs
- Friend From The West Part 2: Hand over $6000

PEACEKEEPER:
- Fishing Gear: Find the body and message on Shoreline pier
- Spa Tour series: Various tasks on Shoreline resort
- The Guide: Survive a raid on every map without dying between them
- Wet Job series: Kill Scavs with suppressed weapons

MECHANIC:
- Gunsmith series (Parts 1-16): Modify weapons to exact specs
- Signal series: Place jammers on maps
- Farming series: Find and hand over items

RAGMAN:
- Sew It Good series: Find and turn in specific clothing items
- The Stylish One: Kill Killa on Interchange

JAEGER:
- Introduction: Find Jaeger's message on Woods (near crashed plane)
- Survivalist Path series: Difficult combat challenges
- Tarkov Shooter series: Kill PMCs with bolt-action on specific maps
- The Huntsman Path series: Various hunting challenges

KEY MAPS & EXTRACTS:
- Customs: ZB-1011 (key), ZB-1012 (key), Dorms V-Ex (roubles), Crossroads, Trailer Park
- Woods: Outskirts, ZB-014 (key), UN Roadblock, Bridge V-Ex
- Interchange: Emercom, Railway, Saferoom Exfil (key), Hole in Fence
- Shoreline: Tunnel, Road to Customs, CCP Temporary, Rock Passage
- Reserve: Cliff Descent (paracord+RR), Sewer Manhole, Bunker D-2
- Labs: Elevator, Ventilation, Parking (keycards)
- Streets: Collapsed Crane, Evacuation Zone, Klimov Street
- Lighthouse: Path to Shoreline, Side Tunnel

AMMO TIERS (BEST PER CALIBER):
- 5.45: BS, BT, BP (budget)
- 5.56: M995, M856A1, M855A1
- 7.62x39: BP is king, PS is budget
- 7.62x51: M61, M62, M80
- 9x19: PBP, AP 6.3
- 12ga: AP-20 slug
- .338: AP, FMJ

BOSSES:
- Reshala: Customs (dorms/new gas), guards with gold TT
- Killa: Interchange (center), Brutus armor
- Glukhar: Reserve (buildings), guards with ASh-12
- Shturman: Woods (sawmill), SVD sniper
- Sanitar: Shoreline (resort/pier), medical loot
- Tagilla: Factory, welding mask, hammer
- Knight/Birdeye/BigPipe: Lighthouse/Woods/Customs, rogue group
'''
