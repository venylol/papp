/*
 * crosstable.c: pour afficher et sauvegarder un tableau avec tous les resulats
 * ainsi qu'une page web avec un tableau complet des resultats
 *
 * (EL) 02/07/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33 Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 19/11/2007 : v1.32 : La sortie en HTML prend un fichier de template qui definit toute
                     la page HTML. On doit y trouver la definition de la cellule individuelle
                     ainsi que l'emplacement de la crosstable. Nouvelles fonction 'RecordCell()',
                     'LookForTMPLFile()' et 'OutputCrosstable()'.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : changement des 'fopen()' en 'myfopen_in_subfolder()'
 * (EL) 12/02/2007 : Modification de 'HTMLCrosstableOutput()', 'TextCrosstableOutput()'
 *                   pour qu'ils affichent le fullname du tournoi au debut.
 * (EL) 02/02/2007 : changement du type de 'Departage' en double
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 * crosstable.c: to display and save an array with all results; saves a html page with
 * a full crosstable.
 *
 * (EL) 02/07/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, no change.
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, HTML output uses a template file that defines all html page.
 *                    The individual cell definition is there and the crosstable place.
 *                    New functions: 'RecordCell()', 'LookForTMPLFile()' and
 *                    'OutputCrosstable()'.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : change all 'fopen()' to 'myfopen_in_subfolder()'
 * (EL) 12/02/2007 : Modify 'HTMLCrosstableOutput()', 'TextCrosstableOutput()'
 *                    so that it displays tournament fullname at the start.
 * (EL) 02/02/2007 : tieBreak[] is changed to 'double' type
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <ctype.h>
#include <time.h>
#include "changes.h"
#include "global.h"
#include "player.h"
#include "couplage.h"
#include "version.h"
#include "more.h"
#include "discs.h"
#include "tiebreak.h"
#include "crosstable.h"



#ifdef ENGLISH

  #define HTML_PROMPT_1   "Enter name of HTML file (return = no save) : "
  #define HTML_PROMPT_2   "Saving crosstable in HTML...\n"
  #define CROSS_PROMPT_1  "Print the ASCII cross-table after which round (1-%ld)? "
  #define CROSS_PROMPT_2  "Enter name of cross-table text file (return = no save) : "
  #define CROSS_APPEND    "Append to existing file (Y/N) ? "
  #define CANT_OPEN       "Can't open file "
  #define BAD_TMPL_FILE   "Bad cell.tmpl file. Cannot find <!--CELL-BEGIN or END-->.\n"
  #define HIT_ANY_KEY     "Hit any key to return to the main menu."
  #define BIP             "BYE"
  #define BIP5            " BYE "

#else

  #define HTML_PROMPT_1   "Entrez le nom du fichier HTML (return = pas de sauvegarde) : "
  #define HTML_PROMPT_2   "Sauvegarde du tableau complet en HTML...\n"
  #define CROSS_PROMPT_1  "Afficher le tableau croise ASCII apres quelle ronde (1-%ld) ? "
  #define CROSS_PROMPT_2  "Entrez le nom du fichier texte (return = pas de sauvegarde) : "
  #define CROSS_APPEND    "Ajout a la fin du fichier (O/N) ? "
  #define CANT_OPEN       "Impossible d'ouvrir le fichier "
  #define BAD_TMPL_FILE   "Mauvais fichier cell.tmpl. <!--CELL-BEGIN or END--> introuvable.\n"
  #define HIT_ANY_KEY     "Pressez une touche pour revenir au menu principal."
  #define BIP             "BIP"
  #define BIP5            " BIP "

#endif


/*
 * Variables globales pour stocker tous les resultats
 ****
 * Global variables used to store all results
 */

typedef struct {
    long      round;           /* ID de la ronde - round ID */
    long      table;           /* ID de la table ou a eu lieu la partie - table ID */
    long      present ;        /* 1 si le joueur etait present a cette ronde, 0 sinon
                                * 1 if player was present, 0 otherwise */
    long      bip ;            /* 1 si le joueur a joue contre bip, 0 sinon
                                * 1 if player played BYE, 0 otherwise */
    long      oppID ;          /* ID FFO (Elo) de l'adversaire - FFO ID of opponent */
    discs_t  discs ;           /* score de la partie, du point de vue du joueur
                                * score of game from the player's side */
    char     color ;           /* couleur dans la partie - color in game */
    long      points ;         /* 2=victoire, 1=nulle, 0=defaite  - 2=victory, 1=draw, 0=loss */
    long      total_points ;   /* depuis le debut du tournoi - total points from beginning of tournament */
    long      stand;           /* rang dans le classement : 1=premier, 2=deuxieme, etc.
                                * tournament standing: 1=first, 2=second... */
} round_structure ;

#define BLACK color_1[0]
#define WHITE color_2[0]
#define UNKNOWN_PLAYER -1

#define CELL_MAX_SIZE  10000   /* Taille maximum de la cellule utilise pour chaque case du tableau HTML
                                * maximum size of cell used for each result of the crosstable */
#define LINE_MAX_LENGTH 2500   /* Longueur max d'une ligne du fichier HTML
                                * maximum length of lines in the html file */

/* Les mots-clefs du fichier de template pour la crosstable
 ****
 * template file keywords for the crosstable */

#define MC_CROSSTABLE "$PAPP_CROSSTABLE"
#define CELLBEGIN "<!-- CELL-BEGIN -->\n"
#define CELLEND   "<!-- CELL-END -->\n"

typedef struct {
    long                FFO_ID ;
    round_structure     roundsArray[NMAX_ROUNDS] ;
    long                Total_Points ;
    discs_t             Total_Discs ;
    long                Bucholtz ;
    double              Tiebreak ;
} player_structure;


player_structure *PlayersArray; /* tableau des joueurs du tournoi - Players array */
long partial_round;             /* utilisee pour trier les joueurs dans compar()
                                 * used to sort players in compar() */


/* prototypes des fonctions locales - local functions prototypes */

long find_player_localID(long FFO_ID);
long PresentAllTournament(long FFO_ID);
long Point_per_game(discs_t v);
void local_compute_tiebreak(long rr);
void get_results(void);
int  compar (const void *p, const void *q);
long score_player1_vs_player2(long FFO_ID_1, long FFO_ID_2);
void computeStandingsUntil(long whichRound);
void ReplaceStr(char *str, char *coupon, char color, char *oppon, char *oppon_name,
        char *my_name, char *game_score, char *relative_game_score,
        char *my_score, char *opp_score, long pts, long cumul_pts,
        char *round, char *table, char *rank);
void ReplaceCell(char *the_template, char *cell, round_structure *pr, player_structure *pj);
FILE *GenerateCellFile(void);
void RecordCell(char [],  long, FILE *) ;
short LookForTMPLFile(char *, long) ;
void OutputCrosstable(char [], FILE *) ;
void ProcessHTMLLine(char *) ;
void HTMLCrosstableOutput(FILE *out);
void TextCrosstableOutput(long round, FILE *fp);





/* long find_player_localID(long FFO_ID)
 *
 * Trouve le ID d'un joueur dans le tableau local de tous les resultats a partir
 * de son ID FFO. Renvoie le ID ou UNKNOWN_PLAYER si on ne le trouve pas.
 ****
 * Finds local ID (in all results array) for a player from his FFO ID.
 * Returns local ID or UNKNOWN_PLAYER if player is not found.
 */
long find_player_localID(long FFO_ID) {
    long i ;

    assert(FFO_ID >= 0) ;
    if (!registered_players->n || !FFO_ID)
        return UNKNOWN_PLAYER ;
    for (i = 0 ; i < registered_players->n  ; i++)
        if (PlayersArray[i].FFO_ID == FFO_ID) return i ;
    return UNKNOWN_PLAYER ;
}


/* long PresentAllTournament(long FFO_ID)
 *
 * Indique si un joueur a fait tout le tournoi : 1 si vrai, 0 sinon.
 ****
 * Returns 1 if the player is present during all the tournament, 0 otherwise
 */
long PresentAllTournament(long FFO_ID) {
    long i, n ;
    player_structure *pj ;

    assert(FFO_ID >= 0) ;
    if (!FFO_ID)
        return 0 ;
    n = find_player_localID(FFO_ID) ;
    if (n == UNKNOWN_PLAYER)
        return 0 ;
    pj = &PlayersArray[n] ;
    for (i = 0 ; i < current_round ; i++)
        if (!pj->roundsArray[i].present)
            return 0 ;
    return 1 ;
}


/* long Point_per_game(discs_t v)
 *
 * Renvoie le nombre de point par partie : victoire = 2, nulle = 1, defaite = 0
 ****
 * Returns number of points per game: victory=2, draw=1, loss=0
 */
long Point_per_game(discs_t v) {
    assert(SCORE_IS_LEGAL(v)) ;
    if (IS_VICTORY(v))
        return 2 ;
    else if (IS_DEFEAT(v))
        return 0 ;
    else
        return 1 ;
}


/* long score_player1_vs_player2(long FFO_ID_1, long FFO_ID_2)
 *
 * Renvoie le score, en demi-points, du joueur FFO_ID_1 contre le joueur FFO_ID_2,
 * pendant tout le tournoi. Si les deux joueurs n'ont pas joue ensemble, on renvoie -1.
 ****
 * Returns score (in half-points) of player FFO_ID_1 against FFO_ID_2, during
 * the tournament. If players didn't play together, returns -1.
 */
long score_player1_vs_player2(long FFO_ID_1, long FFO_ID_2) {
    long n1, n2, i, sum;
    long did_play_together = 0;
    player_structure *pj ;
    round_structure *pr ;

    assert(FFO_ID_1 >= 0);
    assert(FFO_ID_2 >= 0);

    n1 = find_player_localID(FFO_ID_1);
    n2 = find_player_localID(FFO_ID_2);

    if ((n1 == UNKNOWN_PLAYER) || (n2 == UNKNOWN_PLAYER))
        return -1;

    sum = 0;
    pj = &PlayersArray[n1] ;
    for (i = 0 ; i < current_round ; i++) {
        pr = &pj->roundsArray[i] ;
        if ((pr->oppID == FFO_ID_2) && !COUPON_IS_EMPTY(pr->discs)) {
            did_play_together = 1;
            sum += Point_per_game(pr->discs);
        }
    }

    if (!did_play_together)
        return -1;
    else
        return sum;
}

/* void local_compute_tiebreak(long rr)
 *
 * Calcul du departage apres la ronde rr (>=0).
 * Appelle la fonction PlayerTiebreak() (tiebreak.c) pour tous les joueurs.
 ****
 * Compute tiebreak after round rr (>=0)
 * Calls function PlayerTiebreak() (tiebreak.c) for all players.
 */
void local_compute_tiebreak(long rr) {
    long i, j, n;
    BQ_results PlArray ;
    player_structure *pj ;
    round_structure *pr ;

    assert ((rr >= 0) && (rr < current_round));
    for (i = 0 ; i < registered_players->n ; i++) {
        pj = &PlayersArray[i] ;
        PlArray.player_points = pj->roundsArray[rr].total_points ;
        for (j = 0 ; j <= rr ; j++) {
            pr = &pj->roundsArray[j] ;
            PlArray.score[j] = pr->discs ;
            if ((!pr->present) || (pr->bip)) {
                PlArray.opp[j] = 0 ;
                PlArray.opp_points[j] = 0 ;
            } else {
                PlArray.opp[j] = pr->oppID ;
                n = find_player_localID(pr->oppID) ;
                PlArray.opp_points[j] = PlayersArray[n].roundsArray[rr].total_points ;
            }
        }
        PlayerTiebreak(&PlArray, rr, &pj->Tiebreak, &pj->Total_Discs, &pj->Bucholtz) ;
    }
}



/* void get_results(void)
 *
 * Recuperation de tous les resultats deja connus et remplissage des structures
 * pour l'affichage du tableau complet et de la page html.
 ****
 * Get all known results and fill in structures to display crosstable and html page
 */
void get_results(void) {
    long i,j, Num_n1, Num_n2, n1, n2, elo_nbr, table_number;
    discs_t v ;
    player_structure *pj ;
    round_structure *pr ;

/* Initialisation des variables - variables initializations */
    for (i = 0 ; i < registered_players->n ; i++) {
        pj = &PlayersArray[i] ;
        pj->FFO_ID = registered_players->list[i]->ID ;
        pj->Total_Points = pj->Bucholtz = 0 ;
        pj->Total_Discs = ZERO_DISC ;
        pj->Tiebreak = 0.0 ;
        for (j = 0 ; j < current_round ; j++) {
            pr = &(pj->roundsArray[j]) ;
            pr->round = j;
            pr->table = 0;
            pr->present = player_was_present(pj->FFO_ID, j) ;
            pr->bip = pr->oppID = pr->points = pr->total_points = 0 ;
            pr->color = '\0' ;
            pr->discs = UNKNOWN_SCORE ;
            pr->stand = 0;
        }
    }
/* Recuperation des resultats ronde par ronde - get results round by round */
    for (i = 0 ; i < current_round ; i++) {
        round_iterate(i);
        while (next_couple(&n1, &n2, &v)) {
            assert(SCORE_IS_LEGAL(v)) ;
            Num_n1 = find_player_localID(n1) ;
            Num_n2 = find_player_localID(n2) ;
            table_number = find_table_number(i, n1, n2);
            /* Donnees pour Noir - Black data */
            pj = &PlayersArray[Num_n1] ;
            pr = &(pj->roundsArray[i]) ;
            pr->round = i;
            pr->table = table_number;
            pr->oppID = n2 ;
            pr->discs = v ;
            pr->color = BLACK ;
            pr->points = Point_per_game(v) ;
            pr->total_points = (i == 0 ? pr->points : pr->points + pj->roundsArray[i-1].total_points) ;
            pj->Total_Points += pr->points ;
            ADD_SCORE(pj->Total_Discs, (v));
            /* Donnees pour Blanc - White data */
            pj = &PlayersArray[Num_n2] ;
            pr = &(pj->roundsArray[i]) ;
            pr->round = i;
            pr->table = table_number;
            pr->oppID = n1 ;
            pr->discs = OPPONENT_SCORE(v) ;
            pr->color = WHITE ;
            pr->points = Point_per_game(OPPONENT_SCORE(v)) ;
            pr->total_points = (i == 0 ? pr->points : pr->points + pj->roundsArray[i-1].total_points) ;
            pj->Total_Points += pr->points ;
            ADD_SCORE(pj->Total_Discs, OPPONENT_SCORE(v));
        }
        /* Qui a joue contre Bip ? - Who played BYE? */
        for (j = 0 ; j < registered_players->n; j++) {
            pj=&PlayersArray[j] ;
            elo_nbr = pj->FFO_ID ;
            pr = &PlayersArray[j].roundsArray[i] ;
            if (!pr->present) {
                pr->total_points = (i == 0 ? 0 : pj->roundsArray[i-1].total_points) ;
                pr->color = '-' ;
            }
            if (pr->present && (polar2(elo_nbr, i) == 0)) {
                pr->bip = 1 ;
                pr->discs = OPPONENT_SCORE(bye_score) ;
                pr->color = '-' ;
                if (IS_DEFEAT(bye_score))
                    pr->points = 2 ;
                else if (IS_VICTORY(bye_score))
                    pr->points = 0 ;
                else
                    pr->points = 1 ;
                pr->total_points = (i == 0 ? pr->points : pr->points+pj->roundsArray[i-1].total_points) ;
                pj->Total_Points += pr->points ;
                ADD_SCORE(PlayersArray[j].Total_Discs, OPPONENT_SCORE(bye_score));
            }
        }
    }
}


/* int compar (const void *p, const void *q)
 *
 * Fonction utilisee par qsort pour trier les joueurs pour le classement. La variable globale
 * 'partial_round' donne le ID de la ronde dont on veut le classement trie.
 *
 * IMPORTANT : cet ordre de tri doit etre le meme que celui implemente
 *             dans la fonction tri_joueurs() de entrejou.c.
 ****
 * Function used by qsort to sort the players for tthe standings. Global variable 'partial_round'
 * gives round ID for which the standings are asked.
 * IMPORTANT: this sort order should be the same as the one in function
 *  sort_players() of entrejou.c
 */
int compar(const void *p, const void *q) {
    player_structure *pj = (player_structure *) p ;
    player_structure *pq = (player_structure *) q ;

    if (pj->roundsArray[partial_round].total_points > pq->roundsArray[partial_round].total_points) return -1 ;
    if (pj->roundsArray[partial_round].total_points < pq->roundsArray[partial_round].total_points) return 1 ;
/*  if (SCORE_STRICTLY_LARGER(pj->Departage, pq->Departage)) return -1 ;
    if (SCORE_STRICTLY_LARGER(pq->Departage, pj->Departage)) return  1 ;
*/
    if (pj->Tiebreak > pq->Tiebreak) return -1 ;
    if (pq->Tiebreak > pj->Tiebreak) return  1 ;
    if (SCORE_STRICTLY_LARGER(pj->Total_Discs, pq->Total_Discs)) return -1 ;
    if (SCORE_STRICTLY_LARGER(pq->Total_Discs, pj->Total_Discs)) return  1 ;

    return (int) compare_strings_insensitive(findPlayer(pj->FFO_ID)->fullname,
            findPlayer(pq->FFO_ID)->fullname);

    /*if (trouver_joueur(pj->FFO_ID)->fullname < findPlayer(pq->FFO_ID)->fullname)
        return -1 ;
    else
        return 1 ;
    */
}



/* void ReplaceStr(char *str, char *coupon, char color, char *oppon, char *nom_adv,
 *               char *my_name, char *game_score, char *relative_game_score,
 *               char *my_score, char *opp_score, long pts, long cumul_pts,
 *               char *round, char *table, char *rank)
 *
 * Remplace dans la chaine *str les codes speciaux par les valeurs passees
 * en parametre pour l'affichage d'une cellule dans le tableau HTML.
 ****
 * Replaces, in string *str, special codes by values passed as parameters.
 * Used to display a cell in the html table.
 *
 *
 * Les codes sont : PAPP_COLOR         = couleur - color
 * Codes are:       PAPP_OPP           = ID de l'adversaire dans le tableau -
 *                                        opponent ID in table
 *                  PAPP_OPP_NAME[n]   = n premiers caracteres du fullname de l'adversaire -
 *                                       first n characters of opponent fullname
 *                  PAPP_MY_NAME[n]    = n premiers caracteres de mon fullname -
 *                                       first n characters of my fullname
 *                  PAPP_BLACK_NAME[n] = n premiers caracteres du fullname de Noir -
 *                                        first n characters of black fullname
 *                  PAPP_WHITE_NAME[n] = n premiers caracteres du fullname de Blanc -
 *                                        first n characters of whitefullname
 *                  PAPP_COUPON        = coupon de la partie (eg: SEELEY Ben 12/52 SHAMAN David) -
 *                                        game result ["coupon"]
 *                  PAPP_SCORE         = score de la partie, vue de joueur (eg 52/12) -
 *                                        game score seen from player's perspective
 *                  PAPP_SCORE_RELATIF = score de la partie en relatif (eg +40) -
 *                                        relative game score
 *                  PAPP_MY_SCORE      = mon nombre de pions (eg 52) -
 *                                        my number of discs
 *                  PAPP_OPP_SCORE     = pions de l'adversaire (eg 12) -
 *                                        opponent number of discs
 *                  PAPP_PTS           = points gagnes dans la partie (0, 0.5 ou 1) -
 *                                        won points for game
 *                  PAPP_TOTAL         = total de points -
 *                                        total number of points
 *                  PAPP_RONDE         = ronde de la partie -
 *                                        game round
 *                  PAPP_TABLE         = table de la partie -
 *                                        game table
 *                  PAPP_RANG          = classement du joueur apres cette partie -
 *                                        player's ranking after this game
 *
 * Attention, on doit avoir strlen(adv)<=3, et la chaine *str
 * doit faire moins de CELL_MAX_SIZE octets.
 ****
 * Beware, we must have strlen(adv)<=3 and *str string must be at most
 * CELL_MAX_SIZE bytes.
 */
void ReplaceStr(char *str, char *coupon, char color, char *oppon, char *oppon_name,
        char *my_name, char *game_score, char *relative_game_score,
        char *my_score, char *opp_score, long pts, long cumul_pts,
        char *round, char *table, char *rank) {
    char *p ;
    char buf[200], lookedUpStr[50];
    long i, n, len;
    char buffer2[CELL_MAX_SIZE + 300];


    p = strstr(str, "PAPP_COLOR");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_COLOR"));
            *p = *(p+2) = ' ' ;
            *(p+1) = color ;
            strcpy(p+3, buffer2);
    }

    p = strstr(str, "PAPP_SCORE_RELATIF");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_SCORE_RELATIF"));
            strcpy(buf, relative_game_score) ;
        /* On aimerait supprimer le 0 en tete des nombres inferieurs a 10. -
         * remove leading 0 in front of <10 numbers */
            if ((buf[1] == '0') && strlen(buf)>2) {
                memmove(&buf[1], &buf[2], strlen(buf)-1) ;
            }
            for (i = strlen(buf); i < 12 ; i++)
               buf[i] = ' ' ;
            len = max_of(strlen(relative_game_score), 3);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
    }

    p = strstr(str, "PAPP_SCORE");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_SCORE"));
            strcpy(buf, game_score) ;
            for (i = strlen(buf); i < 12 ; i++)
               buf[i] = ' ' ;
            len = max_of(strlen(game_score), 3);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
    }

    p = strstr(str, "PAPP_MY_SCORE");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_MY_SCORE"));
            strcpy(buf, my_score) ;
            for (i = strlen(buf); i < 12 ; i++)
               buf[i] = ' ' ;
            len = max_of(strlen(my_score), 2);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
    }

    p = strstr(str, "PAPP_OPP_SCORE");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_OPP_SCORE"));
            strcpy(buf, opp_score) ;
            for (i = strlen(buf); i < 12 ; i++)
               buf[i] = ' ' ;
            len = max_of(strlen(opp_score), 2);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
    }

    p = strstr(str, "PAPP_PTS");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_PTS"));
            switch(pts) {
                    case 0 : strncpy(p, " 0 ", 3) ;
                                    break ;
                    case 1 : strncpy(p, "0.5", 3) ;
                                    break ;
                    case 2 : strncpy(p, " 1 ", 3) ;
                                    break ;
                    default: strncpy(p, "   ", 3) ;
                    break ;
            }
            strcpy(p+3, buffer2);
    }

    p = strstr(str, "PAPP_TOTAL") ;
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_TOTAL"));
            sprintf(buf, "%ld%s", cumul_pts/2, (cumul_pts%2 ? ".5" : "")) ;
            strcat(buf, "      ") ;
            strncpy(p, buf, 5) ;
            strcpy(p+5, buffer2);
    }

    p = strstr(str, "PAPP_COUPON");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_COUPON"));
            strcpy(buf, coupon) ;
            for (i = strlen(coupon); i < 200 ; i++)
               buf[i] = ' ' ;
            len = strlen(coupon);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
    }

    /* on cherche les mots clefs - looking for keywords:
     * "PAPP_MY_NAME[1]", "PAPP_MY_NAME[2]", etc. */
    for (n = 1; n < 50; n++) {
        sprintf(lookedUpStr, "PAPP_MY_NAME[%ld]", n);
        p = strstr(str, lookedUpStr);
        if (p) {
            strcpy(buffer2, p+(long)strlen(lookedUpStr));
            strcpy(buf, my_name) ;
            for (i = strlen(buf); i < 50 ; i++)
               buf[i] = ' ' ;
            len = min_of(strlen(my_name), n);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
        }
    }

    /* on cherche les mots clefs - looking for keywords:
     * "PAPP_OPP_NAME[1]", "PAPP_OPP_NAME[2]", etc. */
    for (n = 1; n < 50; n++) {
        sprintf(lookedUpStr, "PAPP_OPP_NAME[%ld]", n);
        p = strstr(str, lookedUpStr);
        if (p) {
            strcpy(buffer2, p+(long)strlen(lookedUpStr));
            strcpy(buf, oppon_name) ;
            for (i = strlen(buf); i < 50 ; i++)
               buf[i] = ' ' ;
            len = min_of(strlen(oppon_name), n);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
        }
    }

    /* on cherche les mots clefs - looking for keywords:
     * "PAPP_BLACK_NAME[1]", "PAPP_BLACK_NAME[2]", etc. */
    for (n = 1; n < 50; n++) {
        sprintf(lookedUpStr, "PAPP_BLACK_NAME[%ld]", n);
        p = strstr(str, lookedUpStr);
        if (p) {
            strcpy(buffer2, p+(long)strlen(lookedUpStr));
            if (color == BLACK) {
                strcpy(buf, my_name) ;
                for (i = strlen(buf); i < 50 ; i++)
                    buf[i] = ' ' ;
                len = min_of(strlen(my_name), n);
            }
            else {
                strcpy(buf, oppon_name) ;
                for (i = strlen(buf); i < 50 ; i++)
                    buf[i] = ' ' ;
                len = min_of(strlen(oppon_name), n);
            }
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
        }
    }

    /* on cherche les mots clefs - looking for keywords:
     * "PAPP_WHITE_NAME[1]", "PAPP_WHITE_NAME[2]", etc. */
    for (n = 1; n < 50; n++) {
        sprintf(lookedUpStr, "PAPP_WHITE_NAME[%ld]", n);
        p = strstr(str, lookedUpStr);
        if (p) {
            strcpy(buffer2, p+(long)strlen(lookedUpStr));
            if (color == WHITE) {
                strcpy(buf, my_name) ;
                for (i = strlen(buf); i < 50 ; i++)
                    buf[i] = ' ' ;
                len = min_of(strlen(my_name), n);
            }
            else {
                strcpy(buf, oppon_name) ;
                for (i = strlen(buf); i < 50 ; i++)
                    buf[i] = ' ' ;
                len = min_of(strlen(oppon_name), n);
            }
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
        }
    }

    p = strstr(str, "PAPP_OPP");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_OPP"));
            *p = oppon[0] ;
            if (strlen(oppon) == 1)
                    *(p+1) = ' ' ;
            else
                    *(p+1) = oppon[1] ;
            if (strlen(oppon) < 3)
                    *(p+2) = ' ' ;
            else
                    *(p+2) = oppon[2] ;
            strcpy(p+3, buffer2);
    }


    p = strstr(str, "PAPP_RONDE");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_RONDE"));
            strcpy(buf, round) ;
            len = strlen(round);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
    }

    p = strstr(str, "PAPP_TABLE");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_TABLE"));
            strcpy(buf, table) ;
            len = strlen(table);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
    }

    p = strstr(str, "PAPP_RANG");
    if (p) {
            strcpy(buffer2, p+(long)strlen("PAPP_RANG"));
            strcpy(buf, rank);
            len = strlen(rank);
            strncpy(p, buf, len);
            strcpy(p+len, buffer2);
    }
}



/* void ReplaceCell (char *the_template, char *cell, round_structure *pr, player_structure *pj)
 *
 * Inscrit les infos du joueur (pour une ronde donnee) dans une copie de la cellule 'the_template'.
 ****
 * Writes player info (for a given round) in a copy of cell 'the_template'.
 *
 */
void ReplaceCell(char *the_template, char *cell, round_structure *pr, player_structure *pj) {

    char game_score[10], relative_game_score[10], my_score[10], opp_score[10];
    char opp_nbr[5], opp_name[50], my_name[50], round_num[5], table_num[5], rank[5];
    char coupon[200];
    long nbr_digits_for_rounds, nbr_digits_for_tables;
    long n , temp;


    strcpy(cell, the_template) ;

    /* creation des infos generales de la partie - creating general infos on the game */
    n = find_player_localID(pr->oppID) ;
    sprintf(rank, "%ld", pr->stand);
    sprintf(my_name,"%s", findPlayer(pj->FFO_ID)->fullname);
    nbr_digits_for_rounds = 2;
    sprintf(round_num, "%0*ld", (int)nbr_digits_for_rounds, pr->round + 1);

    /* deux cas particuliers : si le joueur n'a pas joue, ou s'il a joue contre Bip
     ****
     * two special cases: the player didn't play or played bye */
    if (n == UNKNOWN_PLAYER)
        n = -1 ;
    if (pr->bip) {
        ReplaceStr(cell, BIP, '-', "-", BIP, my_name,
                BIP, BIP, BIP, BIP,
                pr->points, pr->total_points, "-", "-", rank) ;
    } else if (!pr->present) {
        ReplaceStr(cell, " --- ", '-', "-", "-", my_name,
                "---", "---", "--", "--",
                pr->points, pr->total_points, "-", "-", rank) ;
    } else { /* cas normal - normal case */

        /* creation du ID de l'aversaire et de son fullname
         ****
         * creation of opponent ID and his fullname */
        sprintf(opp_nbr, "%ld", n+1) ;
        sprintf(opp_name,"%s", findPlayer(pr->oppID)->fullname);

        /* creation des numeros de table - creation of tables numbers */
        nbr_digits_for_tables = (registered_players->n < 20 ? 1 :
                                 (registered_players->n < 200 ? 2 : 3));
        sprintf(table_num, "%0*ld", (int)nbr_digits_for_tables, pr->table);

        /* creation des scores (tjrs du point de vue du joueur)
         ****
         * creation of scores (always from the player's view */
        temp = display_score_diff;
        display_score_diff = 0; sprintf(game_score, "%s", fscore(pr->discs));
        display_score_diff = 1; sprintf(relative_game_score, "%s", fscore(pr->discs));
        sprintf(my_score, "%s", discs2string(pr->discs));
        sprintf(opp_score, "%s", discs2string(OPPONENT_SCORE(pr->discs)));
        display_score_diff = temp;

        /* creation du coupon - creation of coupon */
        temp = display_score_diff;
        display_score_diff = 0;
        if (pr->color == BLACK)
            sprintf(coupon, "%s  %s  %s", my_name, fscore(pr->discs), opp_name);
        else
            sprintf(coupon, "%s  %s  %s", opp_name, fscore(OPPONENT_SCORE(pr->discs)), my_name);
        display_score_diff = temp;


        ReplaceStr(cell, coupon, pr->color, opp_nbr, opp_name, my_name,
                game_score, relative_game_score, my_score, opp_score,
                pr->points, pr->total_points, round_num, table_num, rank) ;
    }
}


/* FILE *GenerateCellFile(void)
 *
 * Cree un fichier Cell.tmpl pour l'affichage d'un fichier de crosstable HTML
 * ce fichier est cree s'il n'existe pas dans le repertoire.
 ****
 * Create a file called Cell.tmpl to display an html crosstable file.
 * This file is created if it doesn't exist in folder
 *
 */
FILE *GenerateCellFile(void) {
    FILE *fp ;

    if (!(fp = myfopen_in_subfolder("cell.tmpl", "w", "", 0, 0)))
        return NULL ;
    printf("FICHIER TEMPLATE GENERE\n") ;

    fprintf(fp, "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01 Transitional//EN\" \"http://www.w3.org/TR/html4/loose.dtd\">\n") ;
    fprintf(fp, "<html>\n<head>\n<title>PAPP_TOURNAMENT_NAME : #PAPP_RONDE</title>\n") ;
    fprintf(fp, "<style type=\"text/css\">\n"
/* table#crosstable : the table itself */
                "/* table#crosstable : the table itself */\n"
                "table#crosstable {\n"
                "\tborder: 2px solid black;\n"
                "\tborder-collapse: collapse;\n"
                "\tmargin-left: auto;\n"
                "\tmargin-right: auto;\n"
                "\tpadding: 0px;\n"
                "\tbackground-color: #ffffff;\n"
                "\tfont-family: Verdana, Arial, Helvetica, sans-serif;\n}\n") ;
/* crosstable_caption : the text for the caption */
    fprintf(fp, "/* crosstable_caption : the text for the caption */\n"
                ".crosstable_caption {\n"
                "\tfont-weight: bold;\n"
                "\tfont-size: 150%%;\n"
                "\ttext-align: center;\n"
                "\tmargin-bottom: 15px;\n}\n") ;
/* td : each cell of the main table */
    fprintf(fp, "/* td : each cell of the main table */\n"
                "td {\n"
                "\tpadding: 0px;\n"
                "\tborder: 1px solid black;\n}\n") ;
/* td.crosstable_title : the title line */
    fprintf(fp, "/* td.crosstable_title : the title line */\n"
                "td.crosstable_title {\n"
                "\ttext-align: center;\n"
                "\tfont-weight: bold;\n"
                "\tpadding: 5px 5px 5px 5px;\n}\n") ;
/* td.crosstable_name : the name column */
    fprintf(fp, "/* td.crosstable_name : the name column */\n"
                "td.crosstable_name {\n"
                "\ttext-align: left;\n"
                "\tpadding: 0px 10px 0px 10px ;\n"
                "\tfont-weight: bold;\n}\n") ;
/* td.crosstable_country : the country column */
    fprintf(fp, "/* td.crosstable_country : the country column */\n"
                "td.crosstable_country {\n"
                "\ttext-align: center;\n"
                "\tpadding: 0px 3px 0px 3px ;\n"
                "\tfont-size: 90%%;\n"
                "\tfont-weight: bold;\n}\n") ;
/* td.crosstable_points : the total number of points column */
    fprintf(fp, "/* td.crosstable_points : the total number of points column */\n"
                "td.crosstable_points {\n"
                "\tpadding: 0px 3px 0px 3px ;\n"
                "\ttext-align: center;\n"
                "\tfont-weight: bold;\n"
                "\tfont-size: 115%%;\n}\n") ;
/* td.crosstable_tiebreak : the tiebreak column */
    fprintf(fp, "/* td.crosstable_tiebreak : the tiebreak column */\n"
                "td.crosstable_tiebreak {\n"
                "\ttext-align: center;\n"
                "\tpadding: 0px 3px 0px 3px ;\n"
                "\tfont-size: 90%%;\n"
                "\tfont-weight: normal;\n}\n") ;
/* table.crosstable_cell : each individual cell view as a table */
    fprintf(fp, "/* table.crosstable_cell : each individual cell view as a table */\n"
                "table.crosstable_cell {\n"
                "\tmargin-left: auto;\n"
                "\tmargin-right: auto;\n"
                "\ttext-align: center;\n}\n") ;
/* td.cell : each cell inside each individual cell */
    fprintf(fp, "/* td.cell : each cell inside each individual cell */\n"
                "td.cell {\n"
                "\tborder: 0px solid black;\n}\n") ;
    fprintf(fp, "</style>\n") ;
    fprintf(fp, "</head>\n<body bgcolor=\"#FFFFFF\" vlink=\"#CC6600\">\n\n\n\n") ;
    fprintf(fp, CELLBEGIN) ;
    fprintf(fp, "<table class=\"crosstable_cell\">\n") ;
    fprintf(fp, "<tr><td class=\"cell\" align=\"left\"><span STYLE=\"font-size: 90%%; color: #990000;\">PAPP_COLOR</span></td>\n"
                "    <td class=\"cell\" align=\"right\"><span STYLE=\"font-size: 90%%; color: #006600;\">PAPP_OPP</span></td></tr>\n") ;
    fprintf(fp, "<tr><td colspan=\"2\" class=\"cell\" align=\"center\"><span STYLE=\"font-weight: bold; text-align: center; color: #000000;\">PAPP_TOTAL</span></td></tr>\n") ;
    fprintf(fp, "<tr><td class=\"cell\" align=\"left\"><span STYLE=\"font-size: 90%%; color: #000000;\">PAPP_PTS</span></td>\n"
                "    <td class=\"cell\" align=\"right\"><span STYLE=\"font-size: 90%%; color: #660000;\">PAPP_SCORE</span></td></tr>\n") ;
    fprintf(fp, "</table>\n") ;
    fprintf(fp, CELLEND) ;
    fprintf(fp, "\n<div class=\"crosstable_caption\">\n"
                "PAPP_TOURNAMENT_NAME<br>\n"
#ifdef ENGLISH
                "Standings after round PAPP_RONDE<br>\n"
#else
                "Classement apr&egrave;s la ronde PAPP_RONDE<br>\n"
#endif
                "</div>\n" MC_CROSSTABLE "\n") ;
    fprintf(fp, "</body>\n</html>\n") ;
    fclose(fp) ;
    return myfopen_in_subfolder("cell.tmpl", "r", "", 0, 0) ;
}

/* Enregistre les lignes definissant une cellule du tableau - record lines defining a table cell */
void RecordCell(char cellule[],  long taille, FILE *file) {
    short begin=0 ;
    char *buf = malloc(taille+1) ;
    cellule[0] = '\0' ;
    printf("RECORD CELL DANS TEMPLATE\n") ;
    while ( fgets(buf, taille, file) ) {
        if (!begin) {
            if (strstr(buf, CELLBEGIN)) /* le debut de la def. - start of def. */
                begin = 1 ;
            continue ;
        } else { /* on est dans la definition - we're in definition */
            if (strstr(buf, CELLEND)) { /* fin de la def. - end of def. */
                begin = 0 ;
                break ;
            }
            /* On recorde - recording */
            strncat(cellule, buf, taille-strlen(cellule)) ;
        }
    }
    cellule[taille-1] = '\0' ;
}

/* Essaie de lire le fichier de template pour la crosstable. Si non trouve,
 * genere un fichier cell.tmpl par defaut. Ensuite, lit le fichier pour
 * remplir le champ cellule (dont la taille max est donnee) avec la definition de la cellule.
 * renvoie 0 si erreur.
 ****
 * Tries to read the template file for the crosstable. If it isn't found, generate
 * a default cell.tmpl file. Then read the file to fill in the cell field
 * (which max size is given) with the cell definition.
 * Returns 0 in case of error.
 */
short LookForTMPLFile(char *cell, long cellSize) {
    FILE *cellFile ;
    short begin, end ;
    char buf[LINE_MAX_LENGTH] ;

    if ( (cellFile = myfopen_in_subfolder("cell.tmpl", "r", "", 0, 0)) != 0 ) {
        printf("TEMPLATE TROUVE\n") ;
        begin = end = 0 ;
        while ( fgets(buf, LINE_MAX_LENGTH, cellFile) ) {
            if ( strstr(buf, CELLBEGIN) )
                 begin = 1; /* Debut de la definition de la cellule - start of cell def. */
            if ( (strstr(buf, CELLEND)) && begin ) {
                end = 1 ; /* Fin de la definition de la cellule - end of cell def. */
                break ;
            }
        }
        if (begin && end) { /* Il y avait une def. de cellule - There was a cell def. */
            printf("DEF. CELL TROUVEE\n") ;
            fseek(cellFile, 0, SEEK_SET) ;
            RecordCell(cell, cellSize, cellFile) ;
            fclose(cellFile) ;
            return 1 ;
        }
        printf(BAD_TMPL_FILE) ;
        return 0 ;
    } else if ( (cellFile = GenerateCellFile()) != 0) {
        RecordCell(cell, cellSize, cellFile) ;
        fclose(cellFile) ;
        return 1 ;
    } else
        return 0 ;
}

/* Generation du code pour la crosstable, a inserer dans un fichier HTML
 ****
 * Generation of crosstable code, to be inserted in html file.
 */
void OutputCrosstable(char cell[], FILE *out) {
    char cell2[CELL_MAX_SIZE + 100] ;
    player_structure *pj ;
    round_structure *pr;
    long i, j ;

#ifdef ENGLISH
    fprintf(out, "<table id=\"crosstable\">\n") ;
/*    fprintf(out, "<caption>%s<br>Standings after %ld round%c<br>&nbsp</caption>\n",tournament_name, ronde, (ronde==1?' ':'s')); */

    fprintf(out, "<tr>\n"
                 "<td class=\"crosstable_title\">No.</td>\n"
                 "<td class=\"crosstable_title\" align=\"left\">Name</td>\n"
                 "<td class=\"crosstable_title\">Country</td>\n") ;
    for (j = 0 ; j < current_round ; j++)
        fprintf(out, "<td class=\"crosstable_title\">r. %ld</td>\n", j+1) ;
    fprintf(out, "<td class=\"crosstable_title\">Points</td>\n"
                 "<td class=\"crosstable_title\">Tie-break</td>\n</tr>\n") ;
    fflush(out) ;
#else
    fprintf(out, "<table id=\"crosstable\">\n") ;
/*    fprintf(out, "<caption><%s<br>Classement apr&egrave;s %ld ronde%c<br>&nbsp</caption>\n", tournament_name,ronde, (ronde==1?' ':'s'));*/

    fprintf(out, "<tr>\n"
            "<td class=\"crosstable_title\">No.</td>\n"
            "<td class=\"crosstable_title\" align=\"left\">Nom</td>\n"
            "<td class=\"crosstable_title\">Pays</td>\n") ;
    for (j = 0 ; j < current_round ; j++)
        fprintf(out, "<td class=\"crosstable_title\">r. %ld</td>\n", j+1) ;
    fprintf(out, "<td class=\"crosstable_title\">Points</td>\n"
            "<td class=\"crosstable_title\">D&eacute;partage</td>\n</tr>\n") ;
    fflush(out) ;
#endif
    for (i = 0 ; i < registered_players->n ; i++) {
        pj = &PlayersArray[i] ;
        fprintf(out, "<tr><td align=\"center\">%ld</td><td class=\"crosstable_name\">%s</td><td class=\"crosstable_country\">%s</td>\n", i+1,
                findPlayer(pj->FFO_ID)->fullname, findPlayer(pj->FFO_ID)->country);
        for (j = 0 ; j < current_round ; j++) {
            fprintf(out, "<td class=\"crosstable_cell\">\n") ;
            pr = &pj->roundsArray[j] ;
            ReplaceCell(cell, cell2, pr, pj) ;
            fprintf(out, "%s\n</td>\n", cell2) ;
        }
        fprintf(out, "<td class=\"crosstable_points\">%ld%s</td>", pj->Total_Points/2, (pj->Total_Points%2 ? ".5" : "" ) ) ;
        fprintf(out, "<td class=\"crosstable_tiebreak\">[%s]</td>\n</tr>\n\n", tieBreak2string(pj->Tiebreak)) ;
    }
    fprintf(out, "</table>\n") ;
}

/* void ProcessHTMLLine(char str[])
 *
 * Remplace, dans une ligne du code HTML de cell.tmpl, les mots-clefs par leur valeur.
 ****
 * Replaces, in a HTML line of cell template, keywords by theur value.
 ***
 * PAPP_TOURNAMENT_NAME : fullname du tournoi - tournament fullname
 * PAPP_RONDE : ID de la ronde - round ID
 */
void ProcessHTMLLine(char *str) {
    char *p ;
    char buf_tmp[LINE_MAX_LENGTH] ;
    char buf2[LINE_MAX_LENGTH] ;
    long len ;

    p = strstr(str, "PAPP_RONDE");
    if (p) {
        strcpy(buf_tmp, p+(long)strlen("PAPP_RONDE"));
        sprintf(buf2, "%ld", current_round) ;
        len = strlen(buf2);
        strncpy(p, buf2, len);
        strcpy(p+len, buf_tmp);
    }
    p = strstr(str, "PAPP_TOURNAMENT_NAME");
    if (p) {
        strcpy(buf_tmp, p+(long)strlen("PAPP_TOURNAMENT_NAME"));
        len = strlen(tournament_name);
        strncpy(p, tournament_name, len);
        strcpy(p+len, buf_tmp);
    }
}


/* void HTMLCrosstableOutput(FILE *out)
 *
 * Generation d'une page HTML contenant le tableau de tous les resultats connus.
 ****
 * Creation of a HTML page containing a table of all known results.
 *
 */
void HTMLCrosstableOutput(FILE *out) {
    char cell[CELL_MAX_SIZE + 100] ;
    short begin=0 ;
    char buf[LINE_MAX_LENGTH] ;
    FILE *cellFile ;

    if (!LookForTMPLFile(cell, CELL_MAX_SIZE + 100)) { /* on n'a pas trouve un fichier - File wasn't found */
        printf(CANT_OPEN ": cell.tmpl file\n");
        return ;
    }
    /* On a donc enregistre une cellule - So a cell was recorded */
    if ((cellFile = myfopen_in_subfolder("cell.tmpl", "r", "", 0, 0)) != 0) {
        while ( fgets(buf, LINE_MAX_LENGTH, cellFile) ) {
            if (begin) {
                if (strstr(buf, CELLEND)) /* la fin de la def. - end of def. */
                    begin = 0 ;
                continue ;
            } else { /* on n'est pas dans la definition - Not in def. */
            if (strstr(buf, CELLBEGIN)) { /* debut de la def. - start of def. */
                begin = 1 ;
                continue ;
            }
            /* On recopie - copy */
                if (strstr(buf, MC_CROSSTABLE))
                    OutputCrosstable(cell, out) ;
                else {
                    ProcessHTMLLine(buf) ; /* remplacement des mots-clefs - keywords replacement */
                    fputs(buf, out) ;
                }

            }
        }
    } else {
        printf(CANT_OPEN ": cell.tmpl file\n");
    }
}

/* void TextCrosstableOutput(long ronde_aff, FILE *fp)
 *
 * Affiche a l'ecran le tableau complet apres la ronde 'ronde_aff' (>=0)
 * et eventuellement le sauvegarde dans le fichier de pointeur fp (si fp !=NULL).
 ****
 *
 */
void TextCrosstableOutput(long round, FILE *fp) {
#ifdef ENGLISH
 #define STR_ROUND_1  "round "
 #define STR_ROUND_2  "rounds"
 #define STR_DISCS    "(Discs/SO- colours * fl.)\n"
 #define STR
#else
 #define STR_ROUND_1  "ronde "
 #define STR_ROUND_2  "rondes"
 #define STR_DISCS    "(Pions/SO- couleurs * fl.)\n"
#endif

#define LIM 10
    long i, j, n, m, diff_col, line1, reste ;
    round_structure *pr ;
    char PlusMinus[]  = "-=+" ;
    char ZeroEqualOne[] = "0=1" ;
    char Digits[]   = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" ;
    char buf[400] ;
    char buf2[400] ;
    char comma[4] ;
    char col[3] ;
    char c, last_float ;
    char title[] = "No                                                                                      " ;
    long  v, simple_swiss;
    char *TrnName ;


    assert ((round >= 0) && (round < current_round)) ;
    more_init(NULL) ;
    clearScreen() ;

    /* Pour afficher le fullname du tournoi en tete - to display tournament fullname as header */
    TrnName = malloc(strlen(tournament_name)+15) ;
    if (TrnName != NULL) {
        sprintf(TrnName, "*** %s ***\n", tournament_name) ;
        more_line(TrnName) ;
        if (fp)
            fprintf(fp, "*** %s ***\n", tournament_name) ;
        free(TrnName) ;
    }

    for (i = 0; i < registered_players->n ; i++) {
        sprintf(buf , "%3ld. %s", i+1, findPlayer(PlayersArray[i].FFO_ID)->fullname) ;
        more_line(buf) ;
        if (fp)
            fprintf(fp, "%s\n", buf) ;
    }
    more_line("") ;
    if (fp)
        fprintf(fp,"\n") ;

    if ((registered_players->n < 99) && (round < 13)) {
        n = (5*round-1)/2+3 ;
        m = 5*round+9 ;
        strncpy(&title[n], (round == 0 ? STR_ROUND_1 : STR_ROUND_2), 6) ;
        strcpy(&title[m], "Pts    TB") ;
        more_line(title) ;
        if (fp)
            fprintf(fp, "%s   " STR_DISCS, title) ;
        for (i = 0; i < registered_players->n ; i++) {
            sprintf(buf, "%2ld ", i+1) ;
            for (j = 0 ; j <= round ; j++) {
                pr = &PlayersArray[i].roundsArray[j] ;
                if (pr->bip)
                    sprintf(buf2, BIP5) ;
                else if (!pr->present)
                    sprintf(buf2, " --- ") ;
                else
                    sprintf(buf2, "%c%c%2ld ", pr->color, PlusMinus[pr->points], find_player_localID(pr->oppID)+1) ;
                strcat(buf, buf2) ;
            }
            strcpy(comma, (PlayersArray[i].roundsArray[round].total_points%2 ? ".5" : "  ")) ;
            sprintf(buf2, ":%2ld%s [%4s]", PlayersArray[i].roundsArray[round].total_points/2, comma,
                tieBreak2string(PlayersArray[i].Tiebreak)) ;
            strcat(buf, buf2) ;
            more_line(buf) ;
            if (fp) {
                diff_col = 0 ;
                fprintf(fp, "%s ", buf) ;
                strcpy(buf2,"") ;
                for (j = 0 ; j <= round ; j++) {
                    c = PlayersArray[i].roundsArray[j].color ;
                    sprintf(col, "%c", c) ;
                    strcat(buf2, col) ;
                    if (c == BLACK)
                        diff_col-- ;
                    else if (c == WHITE)
                        diff_col++ ;
                }
                pr = &PlayersArray[i].roundsArray[round] ;

                if (round == 0)
                    last_float = ' ' ;
                else if (!pr->present)
                    last_float = ' ' ;
                else if (pr->bip)
                    last_float = 'd' ;
                else {
                    n = find_player_localID(pr->oppID) ;
                    m = PlayersArray[i].roundsArray[round-1].total_points - PlayersArray[n].roundsArray[round-1].total_points ;
                    if (m>0)
                        last_float = 'd' ;
                    else if (m < 0)
                        last_float = 'u' ;
                    else
                        last_float = ' ' ;
                }
                fprintf(fp, "(%3s/%3ld - %s %2ld * %c)\n", discs2string(PlayersArray[i].Total_Discs),
                        PlayersArray[i].Bucholtz, buf2, diff_col, last_float) ;
            }
        }
    } else {
        line1 = min_of(LIM - 1, round) ;
        n = (3*line1)+4 ;
        m = 6*line1+11 ;
        strncpy(&title[n], (round == 0 ? STR_ROUND_1 : STR_ROUND_2), 6) ;
        strcpy(&title[m], "Pts    TB") ;
        more_line(title) ;
        if (fp)
            fprintf(fp, "%s   " STR_DISCS, title) ;
        for (i = 0; i < registered_players->n ; i++) {
            sprintf(buf, "%3ld ", i+1) ;
            line1 = min_of(LIM - 1, round) ;
            for (j = 0 ; j <= line1 ; j++) {
                pr = &PlayersArray[i].roundsArray[j] ;
                if (pr->bip)
                    sprintf(buf2, " " BIP5) ;
                else if (!pr->present)
                    sprintf(buf2, "  --- ") ;
                else
                    sprintf(buf2, ((registered_players->n < 99) ? " %c%c%2ld ": "%c%c%3ld"),
                            pr->color, PlusMinus[pr->points], find_player_localID(pr->oppID)+1) ;
                strcat(buf, buf2) ;
            }
            strcpy(comma, (PlayersArray[i].roundsArray[round].total_points%2 ? ".5" : "  ")) ;
            sprintf(buf2, ":%2ld%s [%4s]", PlayersArray[i].roundsArray[round].total_points/2, comma,
                tieBreak2string(PlayersArray[i].Tiebreak)) ;
            strcat(buf, buf2) ;
            more_line(buf) ;
            if (fp) {
                diff_col = 0 ;
                fprintf(fp, "%s ", buf) ;
                strcpy(buf2,"") ;
                for (j = 0 ; j <= round ; j++) {
                    c = PlayersArray[i].roundsArray[j].color ;
                    sprintf(col, "%c", c) ;
                    strcat(buf2, col) ;
                    if (c == BLACK)
                        diff_col-- ;
                    else if (c == WHITE)
                        diff_col++ ;
                }
                fprintf(fp, "(%3s/%3ld - %s %2ld)\n", discs2string(PlayersArray[i].Total_Discs),
                        PlayersArray[i].Bucholtz, buf2, diff_col) ;
            }
            while (line1 < round) {
                reste = min_of(LIM, round - line1) ;
                sprintf(buf, "    ") ;
                for (j = (line1+1) ; j <= (line1+reste) ; j++) {
                    pr = &PlayersArray[i].roundsArray[j] ;
                    if (pr->bip)
                        sprintf(buf2, " " BIP5) ;
                    else if (!pr->present)
                        sprintf(buf2, "  --- ") ;
                    else
                        sprintf(buf2, ((registered_players->n < 99) ? " %c%c%2ld ": "%c%c%3ld"),
                                pr->color, PlusMinus[pr->points], find_player_localID(pr->oppID)+1) ;
                    strcat(buf, buf2) ;
                }
                more_line(buf) ;
                if (fp)
                    fprintf(fp, "%s\n", buf) ;
                line1 += LIM ;
            }
        }
    }


    more_line("");
    if (fp) fprintf(fp, "\n") ;


    /* Affichage de la matrice des victoires. Notez que l'on determine d'abord
     * si chaque joueur a gagne au plus une fois contre chacun de ses adversaires,
     * auquel cas on pourra afficher la matrice avec des 0,=,1 plutot qu'avec
     * les scores en demi-points
     ****
     * Display victories matrix. First check if each player has won at most once
     * against each of his opponents in which case matrix can be displayed with
     * 0,=,1 instead of half-points.
     */

    simple_swiss = 1;
    for (i = 0; i < registered_players->n ; i++)
        for (j = 0; j < registered_players->n ; j++)
            if (score_player1_vs_player2(PlayersArray[i].FFO_ID, PlayersArray[j].FFO_ID) > 2)
                simple_swiss = 0;

    sprintf(buf, "%ld  ", 1 + registered_players->n);
    m = strlen(buf);

    /* Ecriture de la ligne en haut de la matrice - top matrix line writing */
    for (j = 0; j < m ; j++)
        buf[j] = ' ';
    for (j = 0; j < registered_players->n ; j++)
        buf[m + j] = Digits[ (j+1) % 10 ];
    buf[m + registered_players->n] = '\0';
    more_line(buf);
    more_line("");
    if (fp) fprintf(fp, "%s\n\n", buf) ;

    /* Ecriture de la matrice - matrix writing */
    for (i = 0; i < registered_players->n ; i++) {
        sprintf(buf, "%ld     ", i+1);
        for (j = 0; j < registered_players->n ; j++)  {

            if (i == j)
                buf[m + j] = 'X';
            else {
                v = score_player1_vs_player2(PlayersArray[i].FFO_ID, PlayersArray[j].FFO_ID);
                if (v < 0)
                    buf[m + j] = ' ';
                else
                    buf[m + j] = (simple_swiss ? ZeroEqualOne[v] : Digits[v]);
            }
        }

        buf[m + registered_players->n] = '\0';
        more_line(buf);
        if (fp) fprintf(fp, "%s\n", buf) ;
    }

    more_line("");

    more_close() ;
}



/* long HTMLCrosstableSave(char *nom_fichier_html)
 *
 * Renvoie 1 si on arrive a sauvegarder le tableau croise dans le fichier
 * HTML de nom donne en parametre, et 0 sinon.
 ****
 * Returns 1 if we manage to save the crosstable in the HTML file with
 * filename given in parameter, and 0 otherwise.
 */
long HTMLCrosstableSave(char *htmlFilename) {
    FILE *fp;
    long result;

    assert(PlayersArray != NULL);

    /* on veut une sortie HTML - An HTML output is desired */
    result = 0;
    if (htmlFilename[0]) {
        block_signals();
        fp = myfopen_in_subfolder(htmlFilename, "w", subfolder_name, 1, 0);
        if (fp == NULL) {
            printf(CANT_OPEN " '%s'\n", htmlFilename);
            result = 0;
        }
        else {
            printf(HTML_PROMPT_2) ;
            /* Appeler la fonction HTML - Call HTML function */
            HTMLCrosstableOutput(fp) ;
            fclose(fp) ;
            result = 1;
        }
        unblock_signals();
    }
    return result;
}


/* long TextCrosstableSave(long whichRound, char *textFilename, char *openingMode)
 *
 * Affiche a l'ecran une version texte du tableau croise apres la ronde "quelle_ronde".
 * On peut optionnellement demander de sauvegarder ce tableau dans le fichier texte
 * de nom donne (ainsi que le mode d'ouverture : "a"=append, "w"=write, etc.).
 * Passer NULL dans le nom de fichier pour ne pas sauvegarder dans un fichier.
 *
 * Cette fonction n'echoue jamais, sauf si on n'arrive pas a ouvrir le fichier
 * demande avec les droits d'ecriture demandes.
 ****
 * Display a text version of the crosstable after round "whichRound".
 * It can be saved in the text file of given name and opening mode ('a', 'w',...).
 * If filename is NULL, no save is done.
 * This function never fails except if the file cannot be opened with the given opening mode
 */
long TextCrosstableSave(long whichRound, char *textFilename, char *openingMode) {
    FILE *fp;
    long result;

    assert(PlayersArray != NULL);

    /* On veut une sortie du tableau croise en texte - text crosstable output is desired */
    result = 1;
    fp = NULL ;
    block_signals();
    if (textFilename[0]) {
        fp = myfopen_in_subfolder(textFilename, openingMode, subfolder_name, 1, 0);
        if (fp == NULL) {
            printf(CANT_OPEN " '%s'\n", textFilename);
            result = 0;
        }
    }
    /* Affichage (et eventuellement sauvegarde) du tableau - Display (and eventually save) of table */
    TextCrosstableOutput(whichRound, fp) ;
    if (fp)
        fclose(fp) ;
    unblock_signals();

    return result;
}

/* void computeStandingsUntil(long quelle_ronde)
 *
 * On calcule les rangs de chaque joueur (ses classements
 * successifs dans le tournoi) jusqu'a la ronde "whichRound".
 ****
 * Players standings are calculated after each round until round 'whichRound'.
 */
void computeStandingsUntil(long whichRound) {
    long rr, i;
    player_structure *pj ;
    round_structure *pr;

    assert(PlayersArray != NULL);


    for (rr = 0; rr <= whichRound; rr++)  {

        /* Trier les joueurs apres la ronde rr - sort players after round rr */
        partial_round = rr ;
        local_compute_tiebreak(partial_round) ;
        SORT(PlayersArray, registered_players->n, sizeof(player_structure), compar);

        /* Mettre dans le grand tableau le rang de chaque joueur a la ronde rr
         ****
         * Put in big array player standing at round rr */
        for (i = 0 ; i < registered_players->n ; i++) {
            pj = &PlayersArray[i] ;
            pr = &pj->roundsArray[rr] ;
            pr->stand = i+1;
        }
    }
}


/* void PrepareCrosstableCalculations(long whichRound)
 *
 * Recupere tous les resultats et calcule les departages,
 * les totaux de pions, etc. jusqu'a la ronde "whichRound".
 ****
 * Gather all results and compute tie-breaks, discs total, etc.
 * until round 'whichRound'
 */
void PrepareCrosstableCalculations(long whichRound) {
    assert(PlayersArray != NULL);

    get_results() ;
    computeStandingsUntil(whichRound);

    partial_round = whichRound ;
    local_compute_tiebreak(partial_round) ;
    SORT(PlayersArray, registered_players->n, sizeof(player_structure), compar);
}


/* void DisplayCrosstable(void)
 *
 * La fonction principale du module crosstable.c : propose a l'utilisateur
 * de choisir les noms des fichiers HTML et texte pour enregistrer
 * un tableau croise complet contenant tous les resultats du tournoi.
 ****
 * Main crosstable.c module function: offer the user to chose HTML and text
 * filename to save a complete crosstable with all results.
 */
void DisplayCrosstable(void) {
    char htmlFilename[256], textFilename[256], openingMode[2];
    long append ;
    long roundDisplayed ;


    if (current_round < 1)
        return;

    assert(PlayersArray != NULL);
    PrepareCrosstableCalculations(current_round - 1);

    clearScreen();
    /* Veut-on une sortie HTML ? - HTML output wanted? */
    printf(HTML_PROMPT_1) ;
    strcpy(htmlFilename, read_Line());
    putchar('\n');

    if (htmlFilename[0])
        HTMLCrosstableSave(htmlFilename);
    else
        clearScreen();

    /* A quelle ronde faudra-t-il fabriquer le tableau croise texte ?
     ****
     * For which round should we compute text crosstable? */
    printf(CROSS_PROMPT_1, current_round) ;
    if (sscanf(read_Line(), "%ld", &roundDisplayed) != 1)
        return ;
    putchar('\n');
    if ((roundDisplayed < 1) || (roundDisplayed>current_round))
        return ;
    assert( roundDisplayed >= 1 && roundDisplayed <= current_round);


    roundDisplayed-- ;
    partial_round = roundDisplayed;
    local_compute_tiebreak(partial_round);
    SORT(PlayersArray, registered_players->n, sizeof(player_structure), compar);


    /* Veut-on une sortie dans un fichier texte ? - text output wanted? */
    printf(CROSS_PROMPT_2) ;
    strcpy(textFilename, read_Line());
    putchar('\n');

    /* Si oui, veut-on ajouter a la fin du fichier
     * ou ecraser le contenu actuel du fichier ?
     ****
     * If so, do we want to append or erase actual content?
     */
    if (textFilename[0]) {
        append = yes_no(CROSS_APPEND) ;
        if (append)
            sprintf(openingMode, "a");
        else
            sprintf(openingMode, "w");
    }

    TextCrosstableSave(roundDisplayed, textFilename, openingMode);
}

/* Les deux fonctions ci-dessous permettent la gestion en memoire dynamique
 * du (gros) tableau PlayersArray, qui est utilise pour stocker
 * les infos du module crosstable. On evite ainsi de bloquer Papp
 * dans les environnements a memoire limitee.
 ****
 * These two functions enable dynamic memory management for the PlayersArray array,
 * used to save crosstable information. Papp isn't therefore blocked in limited memory
 * environement.
 */

long CanAllocateCrosstableMemory(void) {
    PlayersArray = NULL;
    PlayersArray = malloc((MAX_REGISTERED+1)*sizeof(player_structure));

    if (!PlayersArray)
        return 0;
    else
        return 1;
}

void FreeCrosstableMemory(void) {
    if (PlayersArray)
        free(PlayersArray);
    PlayersArray = NULL;
}
