/*
 * joueurs.c: Toutes les operations sur les joueurs
 *
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, Ajout des fullname de famille et prenom lors de l'enregistrement d'un joueur.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 *
 * joueurs.c: all operations on players
 *
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, Add firstname and fullname when registering a player
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */

/* #define INCLUDE_COUNTRY */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <limits.h>
#include <ctype.h>
#include "changes.h"
#include "global.h"
#include "player.h"
#include "couplage.h"



/* Variables privees (enfin...) - private variables (sort of...) */

static Player *players_database = NULL;
static Insertion_Zone *iz = NULL, **tail_iz = &iz;

Player *firstPlayer(void)        { return players_database; }
Insertion_Zone *firstZone(void) { return iz; }

/*
 * Insertion d'un nouveau joueur dans la base.
 * REMARQUE: il n'est pas necessaire d'initialiser la base !
 * Une nouvelle structure est allouee pour le joueur, et ses champs sont recopies
 * en "memoire permanente" (i.e. il est impossible de la desallouer ; voir la definition
 * de la fonction COPY dans global.c). Cette fonction renvoie un pointeur
 * sur le nouveau joueur, ou NULL si le joueur est deja dans la base.
 *
 ****
 *
 * Inserting a new player in database.
 * Remark: it is not necessary to initialize the database.
 * A new structure is allcoate for the player and all fields are copied in permanent
 * memory (impossible to deallocate; see COPY function in global.c). This function
 * returns a pointer on the new player or NULL if player is already in database.
 */

#ifndef HASHSIZE
# define HASHSIZE 512
#endif

static Player* hashtbl[HASHSIZE];

Player *new_player(long number, const char *name, const char *firstname,
        const char *programmer, const char *country, long rating,
        const char *comment, const long isNewPlayer) {
    Player *pl;
    long i;
    char *buffer;

    assert(name != NULL && country != NULL);
    assert(number > 0);
    if (findPlayer(number))
        return NULL;
    CALLOC(pl, 1, Player);
    buffer = strcpy(new_string(), name);
    if (programmer) {
        strcat(buffer, " (");
        strcat(buffer, programmer);
        strcat(buffer, ")");
    } else if (firstname && *firstname) {
        strcat(buffer, " ");
        strcat(buffer, firstname);
    }
    COPY(name, &pl->family_name);
	if (firstname != NULL) {
        COPY(firstname, &pl->firstname);
	} else {
        COPY("", &pl->firstname);
	}
    COPY(buffer, &pl->fullname);
    COPY(country, &pl->country);
    pl->ID  = number;
	pl->newPlayer = isNewPlayer;
    pl->rating = rating;
    if (comment && *comment)
        COPY(comment, &pl->comment);
    else
        pl->comment = NULL;
    /* Initialiser la table de hachage si necessaire - Initialize hashtable if necessary */
    if (players_database == NULL) {
        for (i=0; i<HASHSIZE; i++)
            hashtbl[i]=NULL;
    }
    /* Puis il faut inserer le joueur dans la base - insert player in database */
    pl->next = players_database;
    players_database = pl;

    /* Et l'inserer dans la table de hachage - insert in hashtable */
    i = (unsigned)number % HASHSIZE;
    pl->nxt2 = hashtbl[i];
    hashtbl[i] = pl;

    return pl;
}

long countPlayersInDatabase(void) {
    long nb = 0;
    Player *pl;

    for (pl = players_database; pl; pl = pl->next)
        ++nb;
    return nb;
}

/*
 * Recherche d'un joueur connaissant son ID Elo ;
 * pour des raisons d'efficacite, nous utilisons le hachage
 *
 ****
 *
 * Search for a player using his Elo ID. Using hash for efficiency
 */
Player *findPlayer(long IDnumber) {
    long i; Player *pl;

    if (IDnumber <= 0 || players_database == NULL)
        return NULL;
    i = (unsigned)IDnumber % HASHSIZE;
    for (pl=hashtbl[i]; pl; pl=pl->nxt2)
        if (pl->ID == IDnumber)
            return pl;
    /* pas trouve - not found */
    return NULL;
}

/* Creation d'une liste de joueurs (initialement vide) - Creation of an empty list of players */
Players_list *createList(void) {
    Players_list *list;

    CALLOC(list, 1, Players_list);
    list->n = 0;
    list->slots_number = 25;
    CALLOC(list->list, list->slots_number, Player*);
    return list;
}

/*
 * Ajout d'un element a une liste.
 * Il peut etre necessaire pour cela de reallouer le bloc de memoire.
 *
 ****
 *
 * Add an element to a list. It may be necessary to reallocate memory block
 */
Players_list *addPlayer(Players_list *lpl, Player *pl) {
    assert(lpl->n <= lpl->slots_number && lpl->list);
    if (lpl->n >= lpl->slots_number) {
        lpl->slots_number += 25;
        REALLOC(lpl->list, lpl->slots_number, Player*);
    }
    assert(lpl->n < lpl->slots_number  && lpl->list);
    (lpl->list)[lpl->n++] = pl;
    return lpl;
}

void emptyList(Players_list *lpl) {
    lpl->n = 0;
}

static int alphabeticalSort(const void *ARG1, const void *ARG2) {
	Player **j1 = (Player **) ARG1 ;
	Player **j2 = (Player **) ARG2 ;

    assert(j1 && *j1 && j2 && *j2);
    return compare_strings_insensitive((*j1)->fullname, (*j2)->fullname);
}

/*
 * Recherche dans la base les joueurs dont le fullname commence par "string"
 * sans distinction min/maj, et renvoie les reponses dans la liste
 * pointee par "lpl"; le resultat renvoye est l'adresse de la liste, soit
 * lpl, sauf si lpl contenait NULL.
 *
 ****
 *
 * Search in database for players whose fullname starts with "string" (case insensitive)
 * and returns answers in the list pointed to by "lpl"; returned result is the list
 * address, that is lpl, unless lpl was NULL.
 */
Players_list *searchName(Players_list *lpl, const char *string) {
    Player *pl;
    long len = strlen(string);

    if (lpl==NULL)
        lpl = createList();
    else
        emptyList(lpl);

    for (pl=players_database; pl; pl=pl->next) {
        if (!different_beginnings(string, pl->fullname, len))
            addPlayer(lpl, pl);
    }
    /* Puis trier la liste par ordre alphabetique - sort list in alphabetical order */
    SORT(lpl->list, lpl->n, sizeof(Player*), alphabeticalSort);
    return lpl;
}

/*
 *  Recherche dans une liste les joueurs dont le fullname commence par "string"
 *  sans distinction min/maj, et renvoie les reponses dans la liste
 *  pointee par "lpl"; le resultat renvoye est l'adresse de la liste, soit
 *  lpl, sauf si lpl contenait 0.
 *  Similaire a la fonction searchName, mais recherche les completions
 *  possibles parmi les joueurs de la liste *completionList au lieu de
 *  les chercher dans toute la base.
 *
 * ***
 *
 * Search in a list for players whose fullname starts with "string" (case insensitive)
 * and returns answers in the list pointed to by "lpl"; returned result is the list
 * address, that is lpl, unless lpl was NULL.
 * Similar to searchName function but look for possible completions within players
 * from list *completionList instead of looking from all database.
 *
 */
Players_list *recherche_nom_dans_liste(Players_list *lpl, const char *string, Players_list *completionList) {
    Player *pl;
    long len = strlen(string);
    long nbrPlayers,i ;

    if (lpl == NULL)
        lpl = createList();
    else
        emptyList(lpl);

    if (completionList != NULL) {
        nbrPlayers = completionList->n;
        if (nbrPlayers > 0)
            for (i = 0; i < nbrPlayers; i++) {
                assert(completionList->list[i]);
                pl = completionList->list[i];
                if (!different_beginnings(string, pl->fullname, len))
                    addPlayer(lpl, pl);
            }
    }

    /* Puis trier la liste par ordre alphabetique - sort list in alphabetical order */
    if (lpl->n > 0)
        SORT(lpl->list, lpl->n, sizeof(Player*), alphabeticalSort);
    return lpl;
}

/*
 * Cree une nouvelle zone d'insertion: [min_ins,max_ins]
 ****
 * Create a new insertion zone: [min_ins,max_ins]
 */
void newInsertionZone(const char *country, long min_ins, long max_ins) {
    Insertion_Zone *z;

    if (min_ins < 0)
        min_ins = 0;
    if (min_ins > max_ins)
        return;     /* La zone est vide! - zone is empty */

    CALLOC(z, 1, Insertion_Zone);
    if (country && *country)
        COPY(country, &z->country);
    else
        z->country = NULL;

    z->min_ins = min_ins;
    z->max_ins = max_ins;
    z->next = NULL;
    *tail_iz = z;
    tail_iz = &(z->next);
}

/*
 * Cherche un ID libre entre <bottom> et <top>, renvoie -1 si
 * aucun n'est disponible.
 ****
 * Look for an available ID between <bottom> and <top>, returns -1
 * if none is available.
 */
static long _insertPlayer(long bottom, long top) {
    long i;

    assert(0 <= bottom && bottom <= top);
    for (i = bottom; i <= top; i++)
        if (findPlayer(i) == NULL)
            return i;
    /* pas trouve - not found */
    return -1;
}

/*
 * Cherche un ID libre pour un joueur du pays indique. Contrairement
 * a la precedente, cette fonction ne doit jamais echouer (cela ne pourrait
 * se produire que si tous les numeros entre 1 et LONG_MAX etaient
 * occupes). Les zones nationales sont toujours scrutees avant les zones
 * internationales.
 *
 ****
 *
 * Look for an available ID for a player from a designate country.
 * Unlike previous function, this one must not fail (that would mean all numbers
 * from 1 to LONG_MAX are used). National zones are always checked before
 * international ones.
 */
long insertPlayer(const char *country) {
    long ret;
    Insertion_Zone *z;

    /* Essayer d'abord les zones nationales - First try national zones */
    for (z = iz; z; z = z->next)
        if (z->country && !compare_strings_insensitive(z->country, country) &&
            (ret = _insertPlayer(z->min_ins, z->max_ins)) >= 0)
            return ret;

    /* Puis les zones internationales - Then international zones */
    for (z = iz; z; z = z->next)
        if (z->country == NULL &&
            (ret = _insertPlayer(z->min_ins, z->max_ins)) >= 0)
            return ret;

    /* En cas d'echec, attribuer le premier ID libre >= 1
     ****
     * In case of failure, assign first free ID >= 1 */
    ret = _insertPlayer(1, LONG_MAX);
    assert (ret > 0);
    return ret;
}

/*
 * char *coupon(player_1, player_2, score): affiche le coupon donnant le score de player_1
 * contre player_2; renvoie une chaine allouee statiquement. Si (player_2=NULL), le second joueur
 * et le score ne sont pas affiches. Si (score<0) alors le score n'est pas affiche.
 *
 ****
 *
 * char *coupon(player_1, player_2, score): display the cooupon giving the score of player_1
 * against player_2, returns a statically allocated string. If (player_2=NULL), the second player
 * and the score are not displayed. If (score<0), the score is not displayed.
 */
char *coupon (const Player *player_1, const Player *player_2, discs_t score) {
    static char buffer[200], tmp[200];
    static long registered = -1;
    static long maxlen;
    long i, v, padding;
    char *fs;
    Player *p;

    /*
     * S'il y a eu de nouveaux inscrits depuis la derniere fois ou cette fonction a ete appelee,
     * nous recalculons 'maxlen', la longueur maximum des demi-coupons.
     *
     ****
     *
     * If there has been new registered players since last time this fonction was called,
     * 'maxlen' is recalculated, corresponding to the maximum length of half-coupon.
     */
    if (registered_players->n != registered) {
        registered = registered_players->n;
        maxlen = 0;
        for (i = 0; i < registered; i++) {
            p = registered_players->list[i];
#ifdef INCLUDE_COUNTRY
            sprintf(tmp, "%s {%s} (%ld)", p->fullname, p->country, p->ID);
#else
            sprintf(tmp, "%s (%ld)", p->fullname, p->ID);
#endif
            if ((v = strlen(tmp)) > maxlen)
                maxlen = v;
        }
    }
    assert(maxlen > 0);
    assert(player_1 != NULL);
#ifdef INCLUDE_COUNTRY
    sprintf(tmp, "%s{%s}(%ld)", player_1->fullname, player_1->country, player_1->ID);
    padding = maxlen - strlen(tmp);
    sprintf(buffer, " %s {%s}%*s(%ld)", player_1->fullname, player_1->country, (int)padding, "", player_1->ID);
#else
    sprintf(tmp, "%s(%ld)", player_1->fullname, player_1->ID);
    padding = maxlen - strlen(tmp);
    sprintf(buffer, " %s%*s(%ld)", player_1->fullname, (int)padding, "", player_1->ID);
#endif
    if (player_2 == NULL)
        return buffer;
    strcat(buffer, " -- ");
#ifdef INCLUDE_COUNTRY
    sprintf(tmp, "%s{%s}(%ld)", player_2->fullname, player_2->country, player_2->ID);
    padding = maxlen - strlen(tmp);
    sprintf(tmp, "%s {%s}%*s(%ld)", player_2->fullname, player_2->country, (int)padding, "", player_2->ID);
#else
    sprintf(tmp, "%s(%ld)", player_2->fullname, player_2->ID);
    padding = maxlen - strlen(tmp);
    sprintf(tmp, "%s%*s(%ld)", player_2->fullname, (int)padding, "", player_2->ID);
#endif
    strcat(buffer, tmp);
    if ((fs = fscore(score)) != NULL) {
        strcat(buffer, "  ");
        strcat(buffer, fs);
    }
    return buffer;
}

#ifdef ENGLISH
#define ERES_I_COMPL    "Player < %s > didn't play this round"
#define ERES_I_AMBIG    "Several players begin with < %s >"
#define ERES_I_INCONNU  "No player begins with < %s > , type TAB to get help"
#else
#define ERES_I_COMPL    "Le joueur < %s > n'a pas joue a cette ronde"
#define ERES_I_AMBIG    "Plusieurs joueurs commencent par < %s >"
#define ERES_I_INCONNU  "Aucun joueur ne commence par < %s > , tapez TAB pour de l'aide"
#endif

/*
 * long numberCompletionInCoupons(ligne,&nroJoueur,chaineScore) :
 * Entree d'un resultat de coupon avec le fullname en toutes lettres. On cherche
 * d'abord dans les coupons non remplis, puis dans les autres.
 *
 * parametre d'entree   : *line
 * parametres de sortie : *playerID
 *                        *scoreString (doit etre allouee a l'exterieur de la fonction)
 *
 * valeurs possibles renvoyees :
 *    -1      pas de fullname de joueur trouve, l'utilisateur a peut-etre utilise les numeros ELOS.
 *     0      le joueur n'est pas dans le tournoi; message d'erreur.
 *     1      reussite :
 *            *playerID contient le ID ELO du joueur
 *            *scoreString contient la fin de la ligne (apres le fullname du joueur).
 *     >=2    nombre de joueurs dans les coupons commencant par ce prefixe;
 *            on en affiche la liste, puis un message d'erreur.
 *
 *****
 * long numberCompletionInCoupons(ligne,&nroJoueur,chaineScore) :
 * Enter a coupon result with complete fullname. Unfilled coupons are first searched,
 * then filled ones.
 *
 * In parameter : *line
 * Out parameters : *playerID
 *                  *scoreString (must be allocated outside the function)
 *
 * Possible return values:
 *    -1     no fullname is found, user may have used ELO IDs.
 *     0     the player is not in the tournament, error message
 *     1     success:
 *            *playerID contains player's ID
 *            *scoreString contains the rest of the line (after player fullname)
 *    >=2    number of players in coupons starting with that prefix;
 *           that list is displayed, then an error message.
 *
 */
long numberCompletionInCoupons(char *line, long *playerID, char *scoreString) {
    long n,i;
    long first,name_length,startScoreString;
    char playerName[BUFFER_SIZE+1];
    char c,previous=0;
    long n1,n2 ;
	long nb_completions;
    discs_t v;
    Player *p1, *p2, *result=NULL;

    *playerID = 0;


     /* on essaie de separer "line" entre un fullname de joueur (playerName)
      * et une chaine correspondant au score (scoreString)
      ****
      * We try to separate 'line' between a player fullname (playerName) and
      * a string corresponding to the score (scoreString)
      */

    n = strlen(line);
    if (n>BUFFER_SIZE) n=BUFFER_SIZE;

    i = 0;
    c = 0;
    first = -1;  /* sera l'index du first caractere different d'une espace -
                  * index of first non-space character */
    while (i<n) {
        previous = c;
        c = line[i];
        if ((first < 0) && (c != ' '))
            first = i;
        if ( isdigit(c) ||
             (c == '=') ||
             (c == '+') ||
             ((c == '-') && (isdigit(line[i+1]))) ||
             ((c == '-') && (previous == ' ')))
            break;
        i++;
    }
    if (first < 0)
        first = 0;
    startScoreString = i;
    name_length = i - first;
    if ((previous == ' ') && (i<n))
        name_length--;

    for (i=0; i<name_length; i++)
        playerName[i] = line[i+first];
    playerName[name_length] = 0;
    name_length = name_length - removeRightSpaces(playerName);

    for (i = startScoreString; i < n; i++)
        scoreString[i-startScoreString] = line[i];
    scoreString[n-startScoreString] = 0;

    /* si le resultat n'a pas ete rentre en toutes lettres, on s'arrete la -
     * if result wasn't fully entered, we stop here */
    if (name_length <= 0)
        return -1;

    /* on recherche si, parmi les coupons non remplis, un joueur commence par "playerName".
        Des qu'il y en a 2 ou plus, on affiche la liste
     ****
     * We look if, among unfilled coupons, a player starts with "playerName".
     *  A soon as there are 2 or more, the list is displayed
     */
    nb_completions = 0;
    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v))
        if (COUPON_IS_EMPTY(v)) {
            p1 = findPlayer(n1);
            if ((p1 != NULL) &&
                !different_beginnings(playerName, p1->fullname, name_length)) {
                nb_completions++;
                if (nb_completions == 2) puts(coupon(result, NULL, UNKNOWN_SCORE));
                if (nb_completions >= 2) puts(coupon(p1, NULL, UNKNOWN_SCORE));
                result = p1;
            }
            p2 = findPlayer(n2);
            if ((p2 != NULL) &&
                !different_beginnings(playerName, p2->fullname, name_length)) {
                nb_completions++;
                if (nb_completions == 2) puts(coupon(result, NULL, UNKNOWN_SCORE));
                if (nb_completions >= 2) puts(coupon(p2, NULL, UNKNOWN_SCORE));
                result = p2;
            }
        }

    if (nb_completions == 1) {
        *playerID = result->ID;
        return nb_completions;
    }

    /* Idem parmi les coupons remplis - Same among filled coupons */
    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v))
        if (COUPON_IS_FILLED(v)) {
            p1 = findPlayer(n1);
            if ((p1 != NULL) &&
              !different_beginnings(playerName, p1->fullname, name_length)) {
                nb_completions++;
                if (nb_completions == 2) puts(coupon(result, NULL, UNKNOWN_SCORE));
                if (nb_completions >= 2) puts(coupon(p1, NULL, UNKNOWN_SCORE));
                result = p1;
            }
            p2 = findPlayer(n2);
            if ((p2 != NULL) &&
              !different_beginnings(playerName, p2->fullname, name_length)) {
                nb_completions++;
                if (nb_completions == 2) puts(coupon(result, NULL, UNKNOWN_SCORE));
                if (nb_completions >= 2) puts(coupon(p2, NULL, UNKNOWN_SCORE));
                result = p2;
            }
        }

    if (nb_completions >= 2)
        printf(ERES_I_AMBIG "\n", playerName);  /* ambiguite - ambiguity */
    if (nb_completions <= 0)
        printf(ERES_I_COMPL "\n", playerName);  /* aucune completion possible ! - No possible completion */

    if ((nb_completions >= 1) && (result != NULL))
        *playerID = result->ID;
    return nb_completions;
}

#ifdef ENGLISH
#define BASE_JOUEURS_GROSSE  "I will not show you all the players in the database...\n" \
                             "Please type at least one letter before using the TAB\n"
#define NOM_TROP_LONG "That player name is too long ! (max %d caracters)\n"
#define ILLEGAL_FIRST "Player names must begin with a letter or a '_'\n"
#define ILLEGAL_ESPACE "Player names must begin with a letter or a '_'\n"
#define ILLEGAL_CARACT "That character is illegal in a player's name : %c\n"
#else
#define BASE_JOUEURS_GROSSE  "Je ne vais pas vous montrer toute la base des joueurs...\n"\
                             "Utilisez la touche TAB apres avoir tape au moins une lettre\n"
#define NOM_TROP_LONG "Ce nom de joueur est trop long ! (max %d caracteres)\n"
#define ILLEGAL_FIRST "Les noms des joueurs doivent commencer par une lettre ou par '_'\n"
#define ILLEGAL_ESPACE "Les noms de joueur ne peuvent pas etre composes que d'espaces...\n"
#define ILLEGAL_CARACT "Ce caractere est illegal dans un nom de joueur : %c\n"
#endif


/*
 * long removeLeftSpaces(string) :
 * Une fonction pour enlever les espaces de tete d'une chaine ;
 * renvoie le nombre d'espaces supprimees ; la chaine est modifiee en place.
 *****
 * long removeLeftSpaces(string):
 * function to remove spaces at the beginning of a string;
 * returns number of removed spaces; string is directly modified
 */
long removeLeftSpaces(char *string) {
    long numberOfSpaces,j,length;

    numberOfSpaces = 0;
    length = strlen(string);

    if ( length > 0 ) {
        while (string[numberOfSpaces] == ' ')
            numberOfSpaces++;
        if (numberOfSpaces > 0) {
            if (numberOfSpaces == length) { /* seulement des espaces ? => les effacer */
                for (j=0 ; j < numberOfSpaces ; j++)
                    string[j]='\0';
            } else {     /* sinon, decaler la chaine pour annuler les espaces de tete */
                for (j=0 ; j <= length-numberOfSpaces ; j++)
                    string[j]=string[j+numberOfSpaces];
            }
        }
    }
    return numberOfSpaces;
}

/*
 * long removeRightSpaces(string) :
 * Une fonction pour enlever les espaces de queue d'une chaine ;
 * renvoie le nombre d'espaces supprimees ; la chaine est modifiee en place.
 *****
 * long removeRightSpaces(string):
 * function to remove spaces at the end of a string;
 * returns number of removed spaces; string is directly modified */
long removeRightSpaces(char *string) {
    long numberOfSpaces,j,length;

    numberOfSpaces = 0;
    length = strlen(string);

    if ( length > 0 ) {
        j = length - 1;
        while ((j >= 0) && (string[j] == ' '))  {
            numberOfSpaces++;
            string[j]='\0';
            j--;
        }
    }
    return numberOfSpaces;
}

/*
 * long SeemsValidPlayerName(buffer) :
 * Verification grossiere de la legalite apparente du fullname d'un nouveau joueur
 * que l'utilisateur rentre au clavier. Il faudrait idealement reconnaitre les
 * memes expressions regulieres que dans PAP.L ; ici on se contente de verifier
 * que le fullname n'est pas trop long, que la premiere lettre est une lettre (a-z et A-Z)
 * ou un underscore (_) , et que les caracteres suivants n'ont pas l'air debiles.
 * Cette fonction pourrait etre rafinee !
 * Renvoie 1 si le fullname a l'air valide, 0 sinon.
 *
 *****
 *
 *  long SeemsValidPlayerName(buffer):
 *  Crude check of the validity of a new player fullname keyboard-entered.
 *  Ideally, same regexp as PAPP.L should be recognized. Here we only check
 *  - that fullname is not too long,
 *  - that first character is a letter (a-z and A-Z) or an underscore (_),
 *  - and that following characters are not too stupid.
 *  This function could be refined!
 *  Returns 1 if fullname looks valid and 0 otherwise.
 */

long SeemsValidPlayerName(char *buffer) {
    long length, k;
    char c;

    length = strlen(buffer);

    if ( (length >= (ALLOC_RING_LENGTH-10)) || (length >= (BUFFER_SIZE-1)) ) {
        printf(NOM_TROP_LONG,BUFFER_SIZE);
        return 0;
    }

    if ( length > 0 ) {
        /* seulement des espaces ? - Only spaces? */
        if (removeLeftSpaces(buffer) == length) {
            printf(ILLEGAL_ESPACE);
            return 0;
        }
        /* le premier caractere a-t-il l'air valide ? - Does first character seems valid? */
        c = buffer[0];
        if ( !isalpha(c) && (c != '_') && (c != ' ')) {
            printf(ILLEGAL_FIRST);
            return 0;
        }

        /* et les autres ? - And the others? */
        length = strlen(buffer);
        for (k=1 ; k<length ; k++) {
            c = buffer[k];
            if ( !isalnum(c) && (c != '_') && (c != ' ') && (c != '(') && (c != ')') &&
                 (c != '\'') && (c != '-') && (c != '.')) {
                printf(ILLEGAL_CARACT , c);
                return 0;
            }
        }
    }

    return 1;
}

/*
 * long selectPlayerFromKeyboard(prompt, fromThesePlayers, &string) :
 * fonction gerant la specification d'un joueur par l'utilisateur, sous
 * la forme de son fullname, d'un prefixe de son fullname ou de son ID Elo.
 *
 * <prompt>             est le prompt affiche en debut de ligne.
 * <fromThesePlayers>   est une liste de joueurs dans laquelle la fonction
 *                      cherche les completions quand l'utilisateur tape
 *                      sur [TAB]. Passer NULL dans <parmi_ces_joueurs>
 *                      pour chercher dans toute la base.
 * <&string>            est un POINTEUR sur la chaine de caractere que
 *                      l'utilisateur a entree. Cette chaine est statique,
 *                      et doit donc etre copie quelque part avant que la
 *                      fonction ne soit invoquee une nouvelle fois.
 *
 * La fonction regarde d'abord si l'utilisateur a tape "*", qui est un joker
 * signifiant "tous les joueurs". La fonction renvoie alors ALL_PLAYERS.
 *
 * Puis on cherche dans la liste <fromThesePlayers> (ou dans toute la base
 * si <fromThesePlayers> est NULL) les joueurs dont le fullname commence par ce qu'a
 * tape l'utilisateur:
 * -- s'il n'y qu'un seul joueur correspondant, on renvoie son ID
 * -- s'il y en a 2 ou plus, on affiche les completions possibles et on recommence
 * -- s'il n'y en a pas, on regarde si l'utilisateur n'aurait pas entre directement
 *    un ID, que l'on renvoie si c'est le cas (attention : on ne verifie pas que
 *    ce ID fait bien partie de la liste <fromThesePlayers>)
 * -- si rien ne marche, on affiche un petit message d'erreur et on recommence.
 *
 * La fonction renvoie -2 si l'utilisateur n'a rien tape.
 *
 ****
 *
 * long selectPlayerFromKeyboard(prompt, fromThesePlayers, &string):
 *  function handling player specification by the user, by way of his fullname,
 *  prefix of his fullname or his ELO ID.
 *
 * <prompt>             id the displayed prompt at start of line
 * <fromThesePlayers>   is a list of players within which the function looks for completion
 *                      when the user presses [TAB]. If NULL, the whole database is searched.
 * <&string>            is a pointer to the string entered by the user. That straing is static,
 *                      it must therefore be copied before the function is called again.
 *
 * The function first checks if user pressed '*' which is a joker meaning 'all players'.
 * In that case, function returns ALL_PLAYERS
 *
 * The list <fromThesePlayers> is searched (or in whole database if <fromThesePlayers< is NULL)
 * for players whose fullname starts with what the user typed:
 * -- if only one player matches, his ID is returned,
 * -- if there are 2 or more, possible completions are displayed and we start over,
 * -- if there is none, we check if user didn't directly enter an ID, which is returned
 *    in that case (no check is done to verify ID is in <fromThesePlayers>)
 * -- if nothing works, an error message is displayed and we start over.
 *
 * Function returns -2 if user didn't type anything.
 */

long selectPlayerFromKeyboard(const char *prompt, Players_list *fromThesePlayers, char **string) {
    long playerID, i ,len;
    char *line;
    Players_list *completions = NULL;

    *string = "";
    while ((line = enter_player_name_kbd(prompt, *string, fromThesePlayers))[0]) {

        len = strlen(line);
        if (removeLeftSpaces(line) == len) {
            printf(ILLEGAL_ESPACE);
            continue;
        }

        *string = line;

        /* joker '*' pour designer tous les joueurs - '*' joker to designate all players */
        if ((line[0] == '*') && (strlen(line) == 1))
            return ALL_PLAYERS;

        if ((!isdigit(line[0])) && (!SeemsValidPlayerName(line))) {
            beep();
            continue;
        }

        if (fromThesePlayers == NULL) {
            if ( (strlen(line) == 0) && (countPlayersInDatabase() >= 100) ) {
                /* ne pas afficher toute la base des joueurs  :-/
                 ****
                 * do not display the whole database  :-/ */
                printf(BASE_JOUEURS_GROSSE);
                if (completions==NULL)
                    completions = createList();
                else
                    emptyList(completions);
            } else
                completions = searchName(completions, line);
        } else
            completions = recherche_nom_dans_liste(completions, line, fromThesePlayers);

        assert( completions != NULL );

        if (completions->n == 1)
            return completions->list[0]->ID;

        if (completions->n >= 2) {
            for (i=0 ; i < completions->n ; i++)
                printf(" %s\n",completions->list[i]->fullname);
            printf(ERES_I_AMBIG "\n", line);  /* ambiguite - ambiguity */
                   continue;
        }

        assert( completions->n == 0);

        /* est-ce directement un ID de joueur ? - Is it a player ID? */
        if (sscanf(line,"%ld", &playerID) == 1)
        return playerID;

        printf(ERES_I_INCONNU "\n", line);  /* aucune completion possible ! - No possible completion! */
       *string = "";
    }

return -2;  /* non trouve si la string est vide - if string is empty */
}
