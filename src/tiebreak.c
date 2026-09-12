/*
 * tiebreak.c: computes player's tiebreak with Brightwell quotient rule.
 *             calcule le departage d'un joueur avec les regles du Brightwell
 *
 * (EL) 18/10/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 02/02/2007 : Tie-break is now double type
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <ctype.h>

#include "changes.h"
#include "global.h"
#include "discs.h"
#include "player.h"
#include "tiebreak.h"


/* NOTE : si (discsTotal <= 1), cela signifie que nous jouons un jeu sans
 * "nombre de pions" tel que les echecs. Le nombre de pions n'est alors
 * rien d'autre que le score et n'apporte donc pas d'information supple-
 * mentaire pour le departage. Nous prenons alors comme departage le
 * "Buchholz", c'est-a-dire la somme des points des adversaires.
 ****
 * NOTE: if (discsTotal <=1), that means we're playing a game without
 * discs count like chess. Number of discs is therefore nothing else
 * than the score and doesn't bring any additionnal information for tie-break.
 * We shall then use "Buchholz" as tie-break, that is sum of points of all opponents.
 */
#define BUCHHOLZ_ONLY   (discsTotal <= 1)

/*
 * void PlayerTiebreak(
 *  BQ_results *PArray, long BQ_round, double *tiebreak, discs_t *TotalDiscsCount, long *SO)
 *
 * Calcule le departage d'un joueur.
 *
 * PArray          : tableau des resultats du joueur avec le ID FFO
 *                   de l'adversaire (0 si Bip ou pas joue), le score et
 *                   le total de points actuel de l'adversaire;
 * BQ_round        : ID de la ronde a laquelle calculer le departage (>=0);
 * tiebreak        : resultat du departage suivant les regles de Brightwell;
 * TotalDiscsCount : total de pions du joueur en comptant toutes les parties
 *                   (meme celles contre Bip ou un adversaire ayant abandonne);
 * SO              : Bucholtz du joueur en comptant toutes les parties
 *                   (meme celles contre Bip ou un adversaire ayant abandonne).
 *
 ****
 *
 * Computes player tiebreak total.
 *
 * PArray          : player results array with FFO ID (or 0 if player played bye
 *                   or didn't play), score and total number of points of opponent,
 * BQ_round        : round ID for which tiebreak is wanted (>=0),
 * tiebreak        : tiebrak total according to Brightwell rules,
 * TotalDiscsCount : total number of discs of the player counting all games
 *                   (even those against bye or a withdrawn opponent),
 * SO              : Bucholtz of player counting all games
 *                   (even those against bye or a withdrawn opponent),
 ****
 *
 * Rules to calculate the Brightwell tie-breaking score : each game is counted normally
 * (using actual disc-count and actual number of points of opponent) unless :
 *   - your opponent withdrew before the end or
 *   - you played Bye or
 *   - you didn't play at all that round because you were late or withdrew;
 *
 * In these cases the Brightwell tie-break is raised using a draw against yourself instead.
 */

void PlayerTiebreak(
    BQ_results *PArray, long BQ_round, double *tiebreak, discs_t *TotalDiscsCount, long *SO) {
    long i, opp, unplayedGames ;

    unplayedGames = 0;
    if (BUCHHOLZ_ONLY)
        brightwell_coeff = 1;
    /* mais voir egalement plus bas: le nombre de pions est ignore
     ****
     * see below: discs total is ignored */
    *SO = 0 ;
    *tiebreak = 0.0 ;
    *TotalDiscsCount = ZERO_DISC ;
    assert ((BQ_round >= 0) && (BQ_round < current_round));
    for (i=0 ; i<=BQ_round ; i++) {
        if (!SCORES_EQUALITY(PArray->score[i], UNKNOWN_SCORE))
            ADD_SCORE(*TotalDiscsCount, PArray->score[i]) ;
        *SO += PArray->opp_points[i] ;
        opp = inscription_ID(PArray->opp[i]) ;
        if ((opp != -1) && present[opp]) {
        /* on a joue un adversaire reel et il est encore present dans le tournoi
         ****
         * player has played a real opponent and he's still in the tournament */
            *tiebreak += brightwell_coeff * (float) PArray->opp_points[i];
            if (!BUCHHOLZ_ONLY)
                *tiebreak += SCORE_TO_FLOAT(PArray->score[i]);
        } else {
        /* on n'a pas joue ou joue Bip ou un adv qui a abandonne
         ****
         * player hasn't played or played bye or a withdrawn opponent. */
            unplayedGames++ ;
        }
    }
    /*
     * Ajouter (unplayedGames) parties nulles contre soi,
     * pour toutes les parties jouees contre BIP, contre un adversaire
     * ayant abandonne ou les parties non jouees (retard ou abandon).
     ****
     * Add (unplayedGames) draw against oneself for all games against bye,
     * against a withdrawn opponent or non-played games (late or resign).
     */

    *tiebreak += brightwell_coeff * ((float) unplayedGames *  PArray->player_points) ;
    if (!BUCHHOLZ_ONLY)
        *tiebreak += (float) (unplayedGames * discsTotal/2);

    if (brightwell_coeff == 0) { /* Departage aux pions - total discs count tiebreak */
        *tiebreak = SCORE_TO_FLOAT(*TotalDiscsCount) ;
    }
}
