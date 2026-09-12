/* player.h
 *
 *  - Gestion des joueurs
 *  - Players management
 *
 * (EL) 15/06/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33 All 'int' become 'long' to force 4 bytes storage.
 					 Addition of field 'newPlayer' in _Player structure.
 					 Enable to know if the player is in the "nouveaux" file. Important for ELO.
					 Ajout du champ 'newPlayer' dans la structure _Player. Cela permet de savoir
                     si le joueur est dans le fichier "nouveaux". Important pour le fichier ELO.
 * (EL) 21/04/2008 : v1.32 Ajout du fullname de famille et prenom dans la structure _Player
 					 Addition of name and firstname in _Player structure.
 					 Necessary to separate these information in the "nouveaux" file
                     Cela est necessaire pour separer ces informations dans le fichier nouveaux.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 02/02/2007 : changement du type 'tieBreak[]' en double
                      tieBreak[] is changed to 'double' type
 * (EL) 13/01/2007 : v1.30 E. Lazard, no change
 */

#ifndef  __Joueur_h__
#define  __Joueur_h__


#include "changes.h"
#include "discs.h"


typedef struct _Player {
	char *family_name;
	char *firstname;        /* Used in 'nouveaux' file */
    char *fullname;         /* Player's full name      */
    char *country;          /* Nationality             */
	long  ID;               /* ID in WOF database      */
    long  rating;           /* World rating            */
    char *comment;          /* Comments                */
	long newPlayer;			/* is a new player ?       */
    struct _Player *next;   /* next in list            */
    struct _Player *nxt2;   /* next for hashing        */
} Player;

typedef struct {
    long slots_number;     /* number of allocated slots */
    long n;                /* list length               */
    Player **list;         /* pointers array            */
} Players_list;

typedef struct _i_zone {
    char *country;          /* country fullname                                */
    long  min_ins;          /* smallest authorized ID - plus petit ID autorise */
	long  max_ins;          /* largest authorized ID - plus grand ID autorise  */
    struct _i_zone *next;   /* next in list - suivant dans la liste            */
} Insertion_Zone;

#define MAX_REGISTERED    256

extern Players_list
            *registered_players,
            *new_players,
            *emigrant_players,
            *team_captain;

extern long         present[MAX_REGISTERED];
extern long         score[MAX_REGISTERED];
extern discs_t      nbr_discs[MAX_REGISTERED];
extern double       tieBreak[MAX_REGISTERED];
extern long         last_float[MAX_REGISTERED];
extern long         team_score[MAX_REGISTERED];

/* Operations, defined in joueurs.c and entrejou.c */

Player *new_player(long number, const char *name, const char *firstname,
        const char *programmer, const char *country, long rating,
        const char *comment, const long isNewPlayer);
Player *findPlayer(long IDnumber);
Player *firstPlayer(void);   /* premier joueur de la base - First player in database */
long countPlayersInDatabase(void);  /* nb de joueurs dans la base - Number of players in database */
long insertPlayer(const char *country);
void newInsertionZone(const char *country, long min_ins, long max_ins);
Insertion_Zone *firstZone(void);
char *coupon(const Player *player_1, const Player *player_2, discs_t score);
char *enter_player_name_kbd(const char *prompt, char *initial_buffer, Players_list *from_these_players);
void enter_players(void);
void display_registered(const char *filename);
void display_teams(const char *filename);
long inscription_ID(long ELO_ID);
long est_nouveau(long numero_elo) ;
long register_player(long ELO_ID);
long change_nationality(long ELO_ID, const char *new_country);
 int sort_players(const void *ARG1, const void *ARG2) ;

/* Gestion des listes - list management */

Players_list *createList(void);
Players_list *addPlayer(Players_list *lpl, Player *j);
Players_list *searchName(Players_list *lpl, const char *string);
Players_list *recherche_nom_dans_liste(Players_list *lpl, const char *string, Players_list *completionList);
void emptyList(Players_list *lpl);

#define ALL_PLAYERS (-1)
long selectPlayerFromKeyboard(const char *prompt, Players_list *fromThesePlayers, char **string);
long numberCompletionInCoupons(char *line, long *playerID, char *scoreString);


/* Utilitaires sur les chaines de caracteres des noms de joueurs - String utilities on players' names */

long removeLeftSpaces(char *string);
long removeRightSpaces(char *string);
long SeemsValidPlayerName(char *buffer);


#endif    /*  #ifndef __Joueur_h__  */
