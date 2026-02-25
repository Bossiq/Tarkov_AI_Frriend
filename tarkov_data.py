"""
Tarkov quest and game knowledge — injected into LLM context.

Comprehensive reference for quests, maps, ammo, bosses, and mechanics
from the 1.0 release (November 2025). This data is injected directly
into the system prompt so the AI can answer specific questions accurately.
"""

QUEST_REFERENCE = '''
=== PRAPOR QUESTS ===
Debut: Kill 5 Scavs, hand over 2 MP-133 shotguns (lvl 1)
Checking: Find bronze pocket watch in blue truck on Customs (need Dorm Room 203 key), give to Prapor
Search Mission: Find Prapor's convoy on Woods, locate USEC camp, extract
Shootout Picnic: Kill 15 Scavs on Woods
BP Depot: Place 3 markers on fuel tankers on Customs
Delivery From The Past: Get secure folder from Tarcone Director office on Customs, stash in Factory break room
Postman Pat Part 1: Find letter in Factory break room, bring to Therapist
Bad Rep Evidence: Find docs case on Customs, give to Skier OR Therapist
Ice Cream Cones: Find 2 ice cream cones on Interchange
Anesthesia: Kill Sanitar and his guards on Shoreline
Polikhim Hobo: Find chemical containers on Factory
Regulated Materials: Find chemical supplies on Reserve
Our Own Land: Kill 25 Scavs on Reserve
Escort: Kill PMCs with bodyguard alive on Reserve
Recon Quest: Recon areas on Reserve

The Punisher Part 1: Kill 15 Scavs on Shoreline with any AK
The Punisher Part 2: Kill 12 Scavs on Shoreline wearing balaclava+scav vest
The Punisher Part 3: Kill 25 Scavs on Customs
The Punisher Part 4: Kill 10 PMCs on Customs wearing PACA+6B47
The Punisher Part 5: Kill 12 PMCs on Shoreline using SVD
The Punisher Part 6: Kill 15 PMCs on any map using SVD (rewards Epsilon container)

Grenadier: Kill 8 PMCs with grenades (any map)

Test Drive Part 1: Kill 5 PMCs from 60+ meters using M1A with Hybrid 46 suppressor and Schmidt & Bender PM II 1-8x24 scope (lvl 18)
Test Drive Part 2: Kill 20 PMCs using MP5SD (with HK upper receiver, suppressor, polymer handguard) on Streets/Ground Zero/Interchange (lvl 22)
Test Drive Part 3: Kill 20 PMCs using AK-12 with suppressor and Valday PS-320 1/6x scope on Lighthouse/Customs/Reserve (lvl 25)

Perfect Mediator: Reach Loyalty Level 4 with all traders

=== THERAPIST QUESTS ===
Shortage: Find and hand over 3 Salewa first aid kits
Painkiller: Find and hand over 4 Analgin painkillers
Sanitary Standards Part 1: Find water testing kit reports in Customs
Sanitary Standards Part 2: Find chemical report on Customs dorms
Operation Aquarius Part 1: Find info about water in dorms on Customs
Operation Aquarius Part 2: Kill 10 Scavs on Customs
General Wounding: Kill 5 PMCs using a pistol
Supply Plans: Obtain documents case in Customs dorms
Car Repair: Find car battery and sparkplugs
Pharmacist: Find and hand over morphine injectors
Health Care Privacy Part 1-5: Series involving medical keys and items on Shoreline Resort
Private Clinic: Find and hand over LEDX skin transilluminator
Colleagues Part 1-3: Find and hand over medical records (Shoreline/Streets)
Vitamins Part 1-3: Medical supply chain quests
Decontamination Service: Kill Scavs in hazmat areas
Crisis: Find emergency supplies
Peacekeeping Mission: Kill 12 Scavs on Customs/Interchange/Shoreline wearing UNTAR gear + M4A1/M16
The Cult Part 1: Find cult ritual sites on Woods/Shoreline

=== SKIER QUESTS ===
Supplier: Hand over 4 body armor and 4 shotguns
Stirrup: Kill 3 PMCs with pistols
What's On The Flash Drive: Find and hand over 2 USB flash drives (filing cabinets, safes)
The Extortionist: Find hidden cargo on Customs, deliver to Skier
Golden Swag: Retrieve gold item from Customs dorms
Kind of Sabotage: Find folder and pass to Therapist
Friend From The West Part 1: Kill 7 USEC PMCs, hand over their dogtags
Friend From The West Part 2: Hand over $6000
Chemical Part 1-4: Locate chemical containers across maps, involves multiple traders
Chumming: Kill 7 PMCs on Interchange
The Blood of War: Find fuel on Customs/Shoreline
Rigged Game: Place markers at medical containers on Shoreline
Resort Part 1-3: Shoreline Resort quests involving keys and items
No Offence: Kill PMCs on Interchange with specific gear
Long Road: Multi-map exploration quest

=== PEACEKEEPER QUESTS ===
Fishing Gear: Find message at Shoreline pier
Tigr Safari: Locate Russian Tigr LAVs on Shoreline
Scrap Metal: Find heavy machinery on Shoreline
Eagle Eye: Find NATO recon drones
Humanitarian Supplies: Secure and deliver supplies
Spa Tour Part 1: Find 3 morphine injectors on Shoreline
Spa Tour Part 2: Find 3 cans of thermite on Shoreline
Spa Tour Part 3: Find 3 alkali bottles on Shoreline
Spa Tour Part 4: Find 3 propane tanks on Shoreline
Spa Tour Part 5: Kill 14 Scavs on Shoreline resort
Spa Tour Part 6: Find 2 water hoses on Shoreline
Spa Tour Part 7: Kill 14 Scavs in resort at night
Cargo X Part 1-4: Investigate TerraGroup cargo
Wet Job Part 1-6: Kill Scavs/PMCs with suppressed weapons across maps
Mentor: Reach high reputation with multiple traders
The Guide: Survive and extract from EVERY map with "Survived" status without dying between them
Samples: Collect specific med samples
TerraGroup Employee: Find TerraGroup info

=== MECHANIC QUESTS ===
Introduction: Find Jaeger's camp note on Woods (near crashed plane) — unlocks Jaeger
Gunsmith Part 1: Build MP-133 to exact specs (see wiki for specific mods)
Gunsmith Part 2-25: Each part requires building a different weapon to exact specifications (MP5, M4, Remington, AKS-74U, etc.)
A Shooter Born In Heaven: Kill 5 PMCs with headshots from 100+ meters on each of: Customs, Shoreline, Reserve, Woods
Signal Part 1-4: Install signal jammers on various maps (includes Lighthouse)
Farming Part 1-4: Related to hideout + bitcoins
Chemistry Closet: Find lab equipment
Psycho Sniper: Kill scavs with headshots using sniper rifle
Scout: Recon multiple locations on various maps
Insider: Find intelligence folders
Surplus Goods: Find and hand over weapon parts
Calibration: Find electronic equipment
Corporate Secrets: Find TerraGroup data on Labs
Energy Crisis: Fix generators on Interchange

=== RAGMAN QUESTS ===
Only Business: Reach level 15 and buy/sell 1M roubles with Ragman
Make ULTRA Great Again: Kill 30 Scavs on Interchange
Big Sale: Place markers at stores on Interchange (Ten, Dino Clothes, Top Brand)
Sew It Good Part 1: Find and hand over Shmaska masks + Pilgrim backpacks
Sew It Good Part 2: Find and hand over Blackrock rigs
Sew It Good Part 3: Find and hand over Wartech gear rigs
Sew It Good Part 4: Find and hand over Gzhel armor
Dressed to Kill: Find Yanka hats and cowboy hats or hand over dogtags
Database Part 1-2: Obtain cargo manifests from OLI/IDEA/Goshan on Interchange
The Key to Success: Retrieve 2 books from Interchange
Sales Night: Kill Scavs at night on Interchange
Hot Delivery: Place markers on Interchange
Living High is Not a Crime Part 1-2: Find luxury items
Textile Part 1-2: Find aramid fiber, ripstop, paracord, cordura, fleece
The Stylish One: Kill Killa on Interchange
Audit: Obtain financial records on Streets of Tarkov
Charisma Brings Success: Reach high charisma skill
No Fuss Needed: Stealth kills on Interchange
Supervisor: Kill scavs with specific gear

=== JAEGER QUESTS ===
Acquaintance: Find Jaeger's camp on Woods (after Mechanic Introduction quest)
The Survivalist Path - Unprotected but Dangerous: Kill Scavs without body armor on Woods
The Survivalist Path - Thrifty: Kill Scavs without using medicine
The Survivalist Path - Zhivchik: Survive dehydration
The Survivalist Path - Wounded Beast: Kill Scavs with broken limbs
The Survivalist Path - Tough Guy: Kill Scavs with pain effect active
The Survivalist Path - Cold Blooded: Kill PMCs while suffering hypothermia
The Survivalist Path - Junkie: Kill Scavs while under stim effects
The Survivalist Path - Eagle-Owl: Kill Scavs at night without NVGs
The Survivalist Path - Combat Medic: Heal certain HP while in raid

Tarkov Shooter Part 1: Kill 3 Scavs on Woods with bolt-action from 40+ meters
Tarkov Shooter Part 2: Kill 3 Scavs on Woods with bolt-action headshots
Tarkov Shooter Part 3: Kill 3 PMCs on Woods with bolt-action
Tarkov Shooter Part 4: Kill 3 PMCs on Woods with bolt-action from 40+ meters
Tarkov Shooter Part 5: Kill 3 Scavs on Reserve with bolt-action
Tarkov Shooter Part 6: Kill 2 PMCs in same raid on Reserve with bolt-action
Tarkov Shooter Part 7: Kill 3 PMCs on any map with bolt-action (no scope)
Tarkov Shooter Part 8: Kill 3 PMCs with headshots on Woods using bolt-action

The Huntsman Path - Secured Perimeter: Kill Scavs on Woods near lumber
The Huntsman Path - Forest Cleaning: Kill Scavs on Woods
The Huntsman Path - Controller: Kill Scavs with suppressed weapons
The Huntsman Path - Evil Watchman: Kill PMCs on Factory
The Huntsman Path - Trophy: Kill Killa on Interchange
The Huntsman Path - Woods Keeper: Kill Shturman on Woods
The Huntsman Path - Factory Chief: Kill Tagilla on Factory
The Huntsman Path - Eraser Part 1: Kill rogues on Lighthouse
The Huntsman Path - Sickness: Find TerraGroup medical items

Shady Business: Find smuggled goods
Nostalgia: Find old photos on Woods
Ambulance: Find medical supplies
Hunting Trip: Kill specific animals on Woods
Insomnia: Kill PMCs at night
Dragnet: Find chemical container on Factory

=== REF (NEW TRADER - 1.0) ===
PvP and Arena focused trader
Offers quests rewarding exclusive items including the Theta container
Tactical challenges across multiple maps
Focus on player-vs-player combat skills

=== BTR DRIVER (NEW TRADER - 1.0) ===
15 quests involving BTR (armored personnel carrier)
Exploration, logistics, and dynamic firefights
Boosting BTR reputation unlocks special gear and travel options

=== MAPS & EXTRACTS ===
Customs: ZB-1011(key), ZB-1012(key), Dorms V-Ex($7000), Crossroads, Trailer Park, RUAF Gate, Old Gas, Smuggler's Boat
Woods: Outskirts, ZB-014(key), UN Roadblock, Bridge V-Ex, Factory Gate(+scav)
Interchange: Emercom, Railway, Saferoom Exfil(key), Hole in Fence, Power Station V-Ex
Shoreline: Tunnel, Road to Customs, CCP Temporary, Rock Passage, Pier Boat
Reserve: Cliff Descent(paracord+RR ice pick), Sewer Manhole(no backpack), Bunker D-2, Armored Train
Labs: Elevator(keycard), Ventilation, Parking Gate, Sewage, Medical Block
Streets of Tarkov: Collapsed Crane, Evacuation Zone, Klimov Street, Pineapple Juice Bar
Lighthouse: Path to Shoreline, Side Tunnel, Armored Train, Southern Road
Ground Zero: Multiple PMC extracts, various Scav extracts
Factory: Gate 3, Cellars, Camera Bunker Door(key)
Terminal: New map added in 1.0 release

=== AMMO TIERS (BEST → BUDGET) ===
5.45x39: BS > BT > BP > PP
5.56x45: M995 > M856A1 > M855A1 > M856
7.62x39: BP > MAI AP > PS > HP
7.62x51: M61 > M62 > M80 > BCP-FMJ
7.62x54R: SNB > LPS Gzh > T-46M
9x19: PBP > AP 6.3 > Pst gzh
9x39: SP-6 > SPP > SP-5
.338 Lapua: AP > FMJ > TAC-X
12.7x55: PS12B > PS12
12ga: AP-20 slug > Flechette > 8.5mm Magnum
.45 ACP: AP > Hydra-Shok

=== BOSSES ===
Reshala: Customs (dorms/new gas station) — 4 guards with gold TTs, drops TT pistol gold
Killa: Interchange (center/KIBA area) — Maska helmet + RPK, drops Adik tracksuit
Shturman: Woods (sawmill) — SVD sniper, 2 guards, drops Red Rebel ice pick (rare)
Sanitar: Shoreline (resort/pier/cottages) — 2 guards, drops rare meds/stims
Tagilla: Factory — welding mask + hammer, rushes players, drops unique gear
Glukhar: Reserve (train station/buildings) — 6 guards with ASh-12/RPK, heavy gear
Knight/Birdeye/BigPipe: Lighthouse/Woods/Customs — 3-man rogue boss group, very dangerous
Kaban: Streets of Tarkov — guards with heavy weapons, controls area near car dealership
The Partisan: Terminal (new 1.0 boss)

=== FLEA MARKET ===
Unlocks at level 15
Can sell most found-in-raid items
Some items are banned from flea (high-tier ammo, certain keys)
Fees increase with price markup

=== HIDEOUT KEY UPGRADES ===
Bitcoin Farm: Passive income, needs graphics cards
Scav Case: Random loot runs (moonshine, intel folder, 95K)
Work Bench: Craft ammo and weapon parts
Medstation: Craft medical supplies (Salewa, IFAK)
Intelligence Center: Reduced scav cooldown
'''
