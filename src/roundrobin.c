/*
 * roundrobin.c: Organisation d'un tournoi toutes rondes (systeme Tastet)
 *
 * Le principe est le suivant: on place les 2n joueurs autour d'une table, et a chaque ronde on fait tourner
 * tous les joueurs, sauf un (le pivot), situe a l'une des extremites de la table.
 * Exemple :
 *
 *   Ronde 1     Ronde 2     Ronde 3
 *
 *  0 1 2 3 4   0 2 3 4 9   0 3 4 9 8     etc.
 *  5 6 7 8 9   1 5 6 7 8   2 1 5 6 7
 *
 * Chaque chaise a sa couleur (mises a part celle du pivot et celle d'en face) et les couleurs alternent :
 *
 *  ? B N B N
 *  ? N B N B
 *
 * Enfin, le pivot commence avec les noirs, et change de couleur a chaque ronde. Les couleurs sont donc initialement
 *
 *  N B N B N
 *  B N B N B
 *
 * S'il y a un nombre impair de joueurs, c'est Bip qui est le pivot, ainsi chaque joueur jouera exactement
 * le meme nombre de fois chaque couleur. Dans le cas d'un plusieurs fois toutes-rondes,
 * on inverse la couleur des chaises apres chaque rotation complete; les appariements sont alors exactement
 * les memes qu'au "tour" precedent, sauf que les couleurs sont inversees.
 *
 * (EL) 21/11/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 *****
 *
 * roundrobin.c : Round-robin tournament organization (Tastet system)
 *
 * Principle: all 2n players are sat around a table and at each round, all players move one position
 * around the table, except one (the pivot) located at one of the table extremities.
 * Example:
 *
 *   Round 1     Round 2     Round 3
 *
 *  0 1 2 3 4   0 2 3 4 9   0 3 4 9 8     etc.
 *  5 6 7 8 9   1 5 6 7 8   2 1 5 6 7
 *
 * Each chair has its color (pivot and opponent apart) and they alternate
 *
 *  ? W B W B
 *  ? B W B W
 *
 * Pivot player starts by playing  Black and changes color at each round. Initial colors are therefore:
 *
 *
 *  B W B W B
 *  W B W B W
 *
 * (EL) 21/11/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */


#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include "couplage.h"
#include "global.h"
#include "player.h"

long     minPlayers4rr = 1,   /* Minimum de joueur pour un toutes-rondes - minimum number of players for round-robin */
         maxPlayers4rr = 6;   /* Maximum de joueurs - maximum number of players */

static long nbrPlayers4rr = 0;        /* Pas encore de table - no table yet */
static long *rrTable;

/*
 * Sauvegarde de la table. Cette fonction est normalement appellee par _save_round(),
 * a la fin de la premiere ronde, immediatement apres initRoundRobin().
 *****
 * Saving the table. This function is normaly called by _save_round() after the first round,
 * immediatly after initRoundRobin().
 */

long save_rrTable (FILE *fp) {
    long i;

    assert(0 < minPlayers4rr && minPlayers4rr <= maxPlayers4rr);
    if (nbrPlayers4rr < minPlayers4rr || nbrPlayers4rr > maxPlayers4rr)
        return 0;       /* rien a sauver - nothing to save */
    assert(rrTable);
    /* Est-ce bien la premiere ronde ? - Is it really first round? */
    assert(current_round == 0);
    fprintf(fp,"round-robin-table [%ld] = {", nbrPlayers4rr);
    for (i = 0; i < nbrPlayers4rr; i++) {
        fprintf(fp, i % (nbrPlayers4rr/2) ? " " : "\n\t");
        fprintf(fp, "%06ld", rrTable[i]);
    }
    fprintf(fp, "\n};\n\n");
    return 1;
}

/*
 * Initialisation de la table a partir d'un tableau; cette fonction est utilisee
 * pour relire la table qui a ete sauvegardee dans le fichier intermediaire.
 *****
 * Initialize a table from an array; this function is used to reread the table
 * that was saved in the intermediate workfile.
 */

long load_rrTable (long card, long *table) {
    long i;

    assert(0 < minPlayers4rr && minPlayers4rr <= maxPlayers4rr);
    if (card < minPlayers4rr && card > maxPlayers4rr)
        return 0;
    /* Est-ce bien la premiere ronde ? - Is it really first round? */
    assert(current_round == 0);
    CALLOC(rrTable, nbrPlayers4rr = card, long);
    for (i = 0; i < card; i++)
        rrTable[i] = table[i];
    return 1;
}

/*
 * Initialisation de la table a l'aide des appariements de la premiere ronde.
 * Sans effet s'il y a trop ou trop peu de joueurs.
 *****
 * Table initialization with first round pairings. No effect if there are
 * too few or too many players.
 */

long initRoundRobin (void) {
    long i, j, howManyPlayers, nbr_unpaired, p0, p1, n1, n2;
    discs_t v;

    assert(0 < minPlayers4rr && minPlayers4rr <= maxPlayers4rr);
    /* Table deja initialisee ? - Table already initialized? */
    if (nbrPlayers4rr > 0)
        return 0;
    /* Est-ce bien la premiere ronde ? - Is it really first round? */
    assert(current_round == 0);
    /* Compter le nombre de joueurs presents - Count number of present players */
    howManyPlayers = 0;
    for (i = 0; i < registered_players->n; i++)
        if (present[i])
            ++howManyPlayers;
    if (howManyPlayers < minPlayers4rr || howManyPlayers > maxPlayers4rr)
        return 0;
    /* Verifier que (presque) tous les joueurs sont apparies - Check that (almost) all players are paired */
    nbr_unpaired = nbr_unpaired_players();
    if (nbr_unpaired > 1)
        return 0;       /* erreur - error */
    nbrPlayers4rr = nbr_unpaired + howManyPlayers;
    assert(nbrPlayers4rr % 2 == 0);
    CALLOC(rrTable, nbrPlayers4rr, long);
    p0 = 0;
    p1 = nbrPlayers4rr/2;
    if (nbr_unpaired) {
        /* Il y a un Bip - There is a bye */
        /* Quel est le joueur qui passe ? - Which player passes? */
        for (i = 0; i < registered_players->n; i++)
            if (present[i]) {
                j = registered_players->list[i]->ID;
                if (polarity(j) == 0) {
                    rrTable[p0++] = -1;
                    rrTable[p1++] = j;
                }
            }
        assert(p0 == 1);
    }
    /*
     * Mettre les joueurs des deux cotes de la table:
     *
     *  N B N B N
     *  B N B N B   pour la premiere ronde.
     *
     *****
     *
     * Put players on each side of the table:
     *
     * B W B W B
     * W B W B W   for the first round
     *
     */
    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v)) {
        if (p0%2 == 0) {
            rrTable[p0++] = n1;   /* Noir  - Black */
            rrTable[p1++] = n2;   /* Blanc - White */
        } else {
            rrTable[p0++] = n2;   /* Blanc - White */
            rrTable[p1++] = n1;   /* Noir  - Black */
        }
    }
    /* Verifier que la table est pleine - Check table is full */
    assert(p0 == nbrPlayers4rr/2);
    assert(p1 == nbrPlayers4rr);
    return 1;       /* Ok! */
}

static void rotation(void) {
    /*
     * Realise la rotation de tous les joueurs sauf le pivot :
     * 1 -> 2 -> 3 -> 4 -> 9 -> 8 -> 7 -> 6 -> 5 -> 1.
     *
     * Cette rotation est dans le mauvais sens (pour des raisons historiques...) ;
     * ce n'est pas trop grave puisqu'elle est d'ordre (nbrPlayers4rr-1).
     *****
     * Rotate all players except pivot:
     * 1 -> 2 -> 3 -> 4 -> 9 -> 8 -> 7 -> 6 -> 5 -> 1.
     *
     * This rotation is in the wrong order (for historical reasons...);
     * it is not an issue as it has order (nbrPlayers4rr-1).
     */
    long i, k, tmp;

    assert(nbrPlayers4rr > 0 && nbrPlayers4rr%2 == 0);
    k = nbrPlayers4rr / 2;
    tmp = rrTable[k];
    for (i = k; i < 2*k-1; i++)
        rrTable[i] = rrTable[i+1];
    rrTable[2*k-1] = rrTable[k-1];
    for (i = k-1; i > 1; i--)
        rrTable[i] = rrTable[i-1];
    rrTable[1] = tmp;
}

/*
 * Realise les appariements pour la ronde dont le ID est donne par la variable globale 'current_round' (plus un).
 * Renvoie 0 en cas d'erreur et 1 si tout s'est bien passe.
 *****
 * Make pairings for the 'current_round' (global variable) round number (+1).
 * returns 0 in case of error and 1 if all went well.
 */

long roundRobin_pairings (void) {
    long i, j, k, howManyPlayers;

    if (nbrPlayers4rr == 0)
        return 0;       /* Infos non disponibles - no infos */
    howManyPlayers = 0;
    for (i = 0; i < registered_players->n; i++)
        if (present[i])
            ++howManyPlayers;
    /* Verifier qu'aucun joueur n'est deja inscrit - check no player is already paired */
    if (nbr_unpaired_players() != howManyPlayers)
        return 0;
    /*
     * Verifier qu'il y a le bon nombre de joueurs.
     * REMARQUE: le code ci-dessous suppose que Bip est en 0 (i.e. c'est le pivot)
     *****
     * Check we have the correct number of players.
     * Remark: code here supposes Bye is with ID 0 (it's the pivot)
     */
    if (howManyPlayers != nbrPlayers4rr - (rrTable[0] < 0))
        return 0;
    /* Verifier que tous les joueurs sont dans la table - Check all players are in the table */
    for (i = 0; i < registered_players->n; i++)
        if (present[i]) {
            j = registered_players->list[i]->ID;
            for (k = 0; k < nbrPlayers4rr; k++)
                if (rrTable[k] == j)
                    break;
            if (k == nbrPlayers4rr)
                return 0;       /* pas trouve - not found */
        }
    /*
     * On fait tourner la table de 'current_round' crans dans le sens direct
     * donc de (nbrPlayers4rr-1-current_round) crans dans le 'mauvais' sens...
     *****
     * Table rotates 'current_round' steps in the direct way therefore
     * (nbrPlayers4rr-1-current_round) steps in the 'wrong' way
     */
    for (i = current_round; i % (nbrPlayers4rr-1); i++)
        rotation();
    /* Et on apparie les joueurs. Le pivot a commence avec les noirs - Players are paired, pivot is black */
    k = nbrPlayers4rr / 2;
    if (rrTable[0] >= 0) {
        /*
         * Le pivot n'est pas Bip; sa chaise change de couleur a chaque ronde,
         * tout comme celle qui est en face de lui.
         *****
         * Pivot is not bye, his chair changes color at each round, like the one in front of him.
         */
        if (current_round%2 == 0)
            make_couple(rrTable[0], rrTable[k], UNKNOWN_SCORE);
        else
            make_couple(rrTable[k], rrTable[0], UNKNOWN_SCORE);
    }
    for (i = 1; i < k; i++) {
        /*
         * Les autres chaises ne changent de couleur que dans le cas d'un plusieurs-fois-toutes-rondes,
         * apres chaque rotation complete de la table.
         *****
         * Other chairs change color only in case of a multiple round-robin,
         * after each complete rotation of the table.
         */
        if (i%2 == (current_round/(nbrPlayers4rr-1))%2)
            make_couple(rrTable[i], rrTable[i + k], UNKNOWN_SCORE);
        else
            make_couple(rrTable[i + k], rrTable[i], UNKNOWN_SCORE);
    }
    /* Et on remet la table en place - Table is put back in place */
    for (i = 0; i < current_round; i++)
        rotation();
    return 1;       /* Ok! */
}
