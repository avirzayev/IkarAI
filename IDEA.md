# General Idea
The game Ikariam is a basic farming strategic game where you build an empire in a multiplayer game. You can trade, create Army, Clans and do many things. This game is http base (api is not exposed but we can simply fetch it).

I want to give a new game to claude and make him to build an empire and be the best in the game.

## Guidelines:
1. Claude decides everything. if he is not sure how the game works or a concept he researches in the web. For example:
    a. What is better to join a clan or setup on my own?
    b. What is the best goverance type to choose?
    c. Where is the best next place to establish city.
I imagine that he will grow a KB about the game and plan it through the long term.
2. Generate him a profile. teach him lessons of politics and make him do a real impact for example. if someone attacked him, maybe he will want to revenge, to concour him. and create a plan of 10 days to gathre resources and attack. I want it to be with "personallity"
3. each day I want a summery of whats new/changed in the city.
4. It runs in a loop python loop. by default it tiggers onces an hour. reads the kb and his current strategy and does what he needs to do. sometimes he can just exit immediatly and wait.
5. he needs to be able to explore the Api of ikariam. there might be new features that will be unloaked as he levels up so it needs to modify itself. I think that the best way is to create in a kb all the relevant http requsets to do an action and once he learns something new it documents it.
