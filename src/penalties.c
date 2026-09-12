/*
 * penalties.c: calcule la table des penalites qui sera envoyee au
 * module appari.c, et realise les appariements automatiques.
 *
 * (EL) 12/04/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 *
 * penalties.c: compute penalties table which be sent to appari.c module
 *  and compute automatic pairings.
 *
 * (EL) 12/04/2020 : v1.37, English version for code.
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
#include <string.h>
#include <assert.h>
#include <limits.h>
#include "global.h"
#include "player.h"
#include "pairings.h"
#include "couplage.h"

static long NumberPlayers, EffectivePlayersNumber, bye, min_score, *tbl, *tbli;
static pen_t **m;
static long chromatic_optimisation (long n1, long n2);
static void optimal_pairing (void);

/*
 * La fonction compute_pairings() se contente de tester si on peut faire un appariement toutes-rondes,
 * et sinon initialise les tableaux necessaires a la fonction optimal_pairing().
 *
 ****
 *
 * compute_pairings() function tests whether a round-robin pairing can be made, and otherwise
 * initialize necessary tables for the optimal_pairing() function.
 */

void compute_pairings(void) {
    long i, n, x, y;

    if (!papp_tournament_api)
        clearScreen();
    if ((NumberPlayers = nbr_unpaired_players()) < 2)
        goto display;

    /* Peut-on faire un toutes-rondes ? - Can a round-robin be made? */
    if (roundRobin_pairings() == 0) {
    /*
     * On note 'EffectivePlayersNumber' le nombre de joueurs effectifs (i.e. incluant Bip)
     * et 'bye' contient l'index de Bip, ou bien 0 si Bip n'existe pas.
     *
     * Chaque joueur est alors repere par trois nombres: son ID Elo 'n', son ID d'inscription 'i'
     * et son index 'x'. L'index sert a indicer la matrice des penalites, et varie entre 0 et EffectivePlayersNumber-1.
     * On construit deux tables tbl et tbli donnant les correspondances :
     *
     *    tbl[x] == n    et    tbli[i] == x.
     *
     * Nous en profitons pour calculer min_score (le score minimum de tous les joueurs presents,
     * pas uniquement ceux qu'on nous demande d'apparier).
     *
     ****
     *
     * 'EffectivePlayersNumber' is the number of effective players (i.e. including Bye) and 'bye' contains
     * bye player index, or 0 if there is no bye.
     *
     * Each player is then tagged by three numbers: its ELO ID 'n', its registration ID 'i' and its index 'x'.
     * The index is used to index penalties matrix and varies from 0 to EffectivePlayersNumber-1. Two lookup tables
     * are built, tbl adn tbli, given correspondances:
     *
     *    tbl[x] == n    and    tbli[i] == x.
     *
     * We also calculate min_score, minimum score for all players not only those we are asked to pair.
     *
     */
        EffectivePlayersNumber = NumberPlayers%2 ? NumberPlayers+1 : NumberPlayers;
        bye  = NumberPlayers%2 ? NumberPlayers   : 0;
        assert(EffectivePlayersNumber%2 == 0);

        /* Construction de la matrice m des penalites - construction of m, penalties matrix */
        CALLOC(m, EffectivePlayersNumber, pen_t*);
        for (i = 0; i < EffectivePlayersNumber; i++) {
            CALLOC(m[i], EffectivePlayersNumber, pen_t);
            for (x = 0; x < EffectivePlayersNumber; x++)
                m[i][x] = 0;
        }

        /* Table des numeros Elo en fonction de l'index - Table of ELO ID based on index */
        min_score = LONG_MAX;
        CALLOC(tbl, EffectivePlayersNumber, long);
        for (x = i = 0; i < registered_players->n; i++) {
            n = registered_players->list[i]->ID;
            if (present[i] && polarity(n) == 0)
                tbl[x++] = n;
            if (present[i] && score[i] < min_score)
                min_score = score[i];
        }
        assert(x == NumberPlayers);
        if (bye)
            tbl[bye] = -1;  /* Afin de bien le distinguer - to distinguish them */
        /*
         * A la premiere ronde, nous appliquons une permutation aleatoire sur cette table,
         * afin que l'appariement choisi ne soit pas toujours le meme.
         *
         * Si le symbole MIXING est defini, nous melangeons toujours les joueurs avant d'optimiser ;
         * attention ! ceci rend les appariements difficilement reproductibles...
         *
         ****
         *
         * On first round, a random permutation is applied on this table so that the pairing
         * is not always the same.
         *
         * If MIXING symbol is defined, all players are mixed before optimization; careful, this causes
         * pairings to be not reproducible.
         *
         */
#ifndef MIXING
        if (current_round == 0)
#endif
        for (x = 0; x < NumberPlayers; x++) {
            y = getRandom(NumberPlayers - x) + x;
            assert(0 <= y && y < NumberPlayers);
            n = tbl[x];
            tbl[x] = tbl[y];
            tbl[y] = n;
        }
        /* Table des index en fonction du no d'inscription - index table from registration number */
        CALLOC(tbli, registered_players->n, long);
        for (i = 0; i < registered_players->n; i++)
            tbli[i] = -1;
        for (x = 0; x < NumberPlayers; x++) {
            i = inscription_ID(tbl[x]);
            tbli[i] = x;
        }
        /*
         * Nous laissons optimal_pairing() faire le reste des initialisations,
         * en particulier le calcul des penalites, puis realiser l'appariement.
         *
         ****
         *
         * We leave optimal_pairing() do the rest of the initializations,
         * in particular penalties computation, and do the pairings
         */
        optimal_pairing();
        free(tbl);
        free(tbli);
        for (i = 0; i < EffectivePlayersNumber; i++)
            free(m[i]);
        free(m);
    }
display:
    if (papp_tournament_api)
        return;
    display_pairings(NULL, 0);
    save_pairings_file();
}

/*
 * Nous ne faisons pas de test de debordement ici, afin que edmonds.c puisse les detecter
 ****
 * No overflow tests here, so that edmonds.c can detect them
 */

#define  ADD_PENALTY(p,x)       (p) += (x)
#define  SUB_PENALTY(p,x)    (p) -= (x)

#ifdef ENGLISH
# define PENA_ANNOUNCE  "Penalties:"
# define PENA_REPLAY    " replay"
# define PENA_COLOURS   " colours"
# define PENA_FLOAT     " float"
# define PENA_COUNTRY   " chauvinism"
# define PENA_ELITISM   " elitism"
# define OPTIMAL_PAIR   "Looking for an optimal pairing..."
# define HIT_ANY_KEY    "Hit any key..."
# define BYE_NAME       "Bye"
#else
# define PENA_ANNOUNCE  "Penalites:"
# define PENA_REPLAY    " repetition"
# define PENA_COLOURS   " couleurs"
# define PENA_FLOAT     " flottement"
# define PENA_COUNTRY   " chauvinisme"
# define PENA_ELITISM   " elitisme"
# define OPTIMAL_PAIR   "Recherche d'un appariement optimal..."
# define HIT_ANY_KEY    "Pressez une touche..."
# define BYE_NAME       "Bip"
#endif

static void optimal_pairing (void) {
    long i, j, n, pl1, pl2, round0, pl1_is_Black;
    long *v, *color_diff, *last_color ;
    discs_t nbr_discs_pl1;
    pen_t result;
#ifdef COMPENSATION_FLOATS
    long **wins_history;
    long *wins_history_buffer;
    long wh_rows, wh_columns;
#endif

    /*
     * Allocation des tableaux et initialisation - array allocation and initialization
     */
    CALLOC(color_diff, registered_players->n, long);
    CALLOC(last_color, registered_players->n, long);
    for (i = 0; i < registered_players->n; i++)
        color_diff[i] = last_color[i] = 0;
    CALLOC(v, EffectivePlayersNumber, long);



#ifdef COMPENSATION_FLOATS
     /*
     * v = wins_history[i,r] dit si le joueur 1 a gagne (v == 2), fait nulle (v == 1) ou perdu (v == 0) a la ronde r.
     ***
     * v = wins_history[i,r] tells if player 1 won (v == 2), drew (v == 1) or lost (v == 0) at round r.
     */

    wh_rows = 1 + registered_players->n;
    wh_columns = 1 + current_round;

    wins_history = (long**)malloc((unsigned) wh_rows * sizeof(long*));
    wins_history_buffer = (long*)malloc((unsigned) wh_rows * wh_columns * sizeof(long));

    for (i = 0; i < registered_players->n; i++)
         wins_history[i] = &wins_history_buffer[i*wh_columns];

    for (i = 0; i < registered_players->n; i++)
        for (j = 0; j < current_round; j++)
            wins_history[i][j] = 0;
#endif


    if (!papp_tournament_api) {
        printf(PENA_ANNOUNCE);
        fflush(stdout);
    }

    /*
     * Penalites de repetition, tout d'abord. Nous faisons une boucle sur les appariements des rondes precedentes,
     * en ajoutant a chaque fois leur contribution a la matrice courante de penalite.
     *
     * Nous en profitons pour compter le nombre de fois ou les joueurs jouent blanc ou noir --
     * plus precisement nous comptons simplement l'ecart (nb de parties avec Noir) - (nb de parties avec Blanc).
     *
     * Nous en profitons egalement pour remplir la table des gains des joueurs dans les rondes passees.
     *
     ****
     *
     * Repetition penalties first. A loop is made on previous pairings, affing each time their contribution
     * to the current penalties matrix.
     *
     * We also count number of times players play black or white -- more precisely, only the gap is counted
     * (number of games with black) - (number of games with white)
     *
     * wins history array is also initialized with previous rounds results
     */
    if (!papp_tournament_api) {
        printf(PENA_REPLAY);
        fflush(stdout);
    }
    for (round0 = 0; round0 < current_round; round0++) {
        long k1, k2;
        round_iterate(round0);
        while (next_couple(&pl1, &pl2, &nbr_discs_pl1)) {
            pl1 = inscription_ID(pl1); assert(pl1 >= 0);
            pl2 = inscription_ID(pl2); assert(pl2 >= 0);

            {
            /* Petit squat des "couleurs" - colors squat */
            ++color_diff[pl1];        /* Noir/Black  = +1 */
            --color_diff[pl2];        /* Blanc/White = -1 */
            if (round0 == current_round-1)
                last_color[pl1] = 1, last_color[pl2] = 2;
            /* fin du squat! - end of squat */
            }

#ifdef COMPENSATION_FLOATS
            {
            /* remplissage de l'historique des gains - wins history initialization */
            if (IS_VICTORY(nbr_discs_pl1))
                wins_history[pl1][round0] = 2;
            else /* gain noir - black win */
            if (IS_DEFEAT(nbr_discs_pl1))
                wins_history[pl2][round0] = 2;
            else { /* gain blanc - white win */
                wins_history[pl1][round0] = 1;
                wins_history[pl2][round0] = 1 ;
            }  /* nulle - draw */
            /* fin du squat! - end of squat */
            }
#endif

            k1 = tbli[pl1];
            k2 = tbli[pl2];
            if (k1 >= 0 && k2 >= 0) {
                assert(k1 != k2);
                ADD_PENALTY(m[k1][k2], same_colors_replay_penalty);
                ADD_PENALTY(m[k2][k1], opposite_colors_replay_penalty);
                if (round0 == current_round-1) {
                    ADD_PENALTY(m[k1][k2], immediate_replay_penalty);
                    ADD_PENALTY(m[k2][k1], immediate_replay_penalty);
                }
            }
        }
        /* Et les joueurs qui jouent contre Bip ? - what about players paired against bye? */
        if (bye)
            for (i=0; i<NumberPlayers; i++)
                if (polar2(tbl[i],round0) == 0) {
                    ADD_PENALTY(m[i][bye], bye_replay_penalty);
                    ADD_PENALTY(m[bye][i], bye_replay_penalty);
                    if (round0 == current_round-1) {
                        ADD_PENALTY(m[i][bye], immediate_replay_penalty);
                        ADD_PENALTY(m[bye][i], immediate_replay_penalty);
                    }
                }
    }

    /*
     * Penalites de couleur, ensuite. L'ecart actuel (noir)-(blanc) de chaque joueur se trouvera augmente
     * ou diminue d'une unite selon la couleur jouee a la presente ronde.
     *
     ****
     *
     * Color penalties second. Actual gap (black)-(white) for each player each incremented or decremented
     * by one unit depending on played color at current round.
     */
    if (!papp_tournament_api) {
        printf(PENA_COLOURS);
        fflush(stdout);
    }
    for (i=0; i<NumberPlayers; i++)
        for (j=0; j<NumberPlayers; j++)
            if (i != j) {
                long f;

                /* Si i joue les noirs - if i plays black */
                n = inscription_ID(tbl[i]); assert(n >= 0);
                f = labs(color_diff[n] + 1);
                if (f > colors_nmax - 1)
                    f = colors_nmax - 1;
                ADD_PENALTY(m[i][j], color_penalty[f]);

                /* Avait-il les noirs a la ronde precedente ? - Did he have black at last round? */
                if (last_color[n] == 1)
                    ADD_PENALTY(m[i][j], repeated_color_penalty);

                /* Si i joue les blancs - if i plays white */
                f = labs(color_diff[n] - 1);
                if (f > colors_nmax - 1)
                    f = colors_nmax - 1;
                ADD_PENALTY(m[j][i], color_penalty[f]);

                /* Avait-il les blancs a la ronde precedente ? - Did he have white at last round? */
                if (last_color[n] == 2)
                    ADD_PENALTY(m[j][i], repeated_color_penalty);
            }
    /* Et si on joue contre Bip ? - And if we play against bye? */
    if (bye)
        for (i=0; i<NumberPlayers; i++) {
            long f;

            n = inscription_ID(tbl[i]); assert (n >= 0);
            f = labs(color_diff[n]);
            if (f > colors_nmax - 1)
                f = colors_nmax - 1;
            ADD_PENALTY(m[i][bye], color_penalty[f]);
            ADD_PENALTY(m[bye][i], color_penalty[f]);
        }

    /*
     * Penalites de flottement, ici. Plutot que d'ajouter directement les contributions a la matrice m[][],
     * nous stockons la penalite de flottement dans 'pflot' et nous ne l'ajoutons a m[][] qu'a la fin.
     *
     ****
     * Float penalties here. Instead of directly adding contribution to the m[][] matrix, the float penalty
     * is saved in pflot and added to m[][] only at the end.
     */
    if (!papp_tournament_api) {
        printf(PENA_FLOAT);
        fflush(stdout);
    }
#ifdef ELITISM
    if (!papp_tournament_api) {
        printf(PENA_ELITISM);
        fflush(stdout);
    }
#endif
    for (i=0; i<EffectivePlayersNumber; i++)
        for (j=0; j<EffectivePlayersNumber; j++)
            if (i < j) {
                long sci, scj, f, af;
                pen_t pflot;

                pl1 = tbl[i]; if (pl1>=0) pl1 = inscription_ID(pl1);
                pl2 = tbl[j]; if (pl2>=0) pl2 = inscription_ID(pl2);
                sci = pl1>=0 ? score[pl1] : min_score-1;
                scj = pl2>=0 ? score[pl2] : min_score-1;
                /* Le flottement de i sur j vaut - i on j float is */
                f = sci-scj;
                /* La penalite principale de flottement vaut - main float penalty is */
                af = labs(f);
                if (af > floats_nmax - 1)
                    af = floats_nmax - 1;
                pflot = float_penalty[af];
                /* Ajouter les penalites de flottement cumule - add cumulated float penalties */
                if (pl1 >= 0 && f * last_float[pl1] > 0)
                    ADD_PENALTY(pflot, cumulative_floats_penalty);
                if (pl2 >= 0 && f * last_float[pl2] < 0)
                    ADD_PENALTY(pflot, cumulative_floats_penalty);


#if (defined(COMPENSATION_FLOATS) )
                /*
                 * Retrancher les minorations (flottement anti-cumule) on ne les retranche que si un joueur
                 * a flotte haut a la ronde precedente et perdu (auquel cas il doit flotter bas) ou si
                 * il a flotte bas et gagne (auquel cas il doit flotter haut)
                 ****
                 * Substract minoration (anti-cumulated floats). They are substracted only if a player floated up
                 * at previous round and lost (and therefore must float down) or he floated down and won
                 * (in which case he must float up).
                 */
                if (current_round > 1) {
                    if (( pl1 >= 0   &&   f * last_float[pl1] < 0) &&
                      ((last_float[pl1] < 0  &&  wins_history[pl1][current_round-1] == 0 ) ||
                      (last_float[pl1] > 0  &&  wins_history[pl1][current_round-1] == 2 ) ))
                        SUB_PENALTY(pflot, opposite_float_pen);
                    if (( pl2 >= 0   &&   f * last_float[pl2] > 0) &&
                      ((last_float[pl2] < 0  &&  wins_history[pl2][current_round-1] == 0 ) ||
                      (last_float[pl2] > 0  &&  wins_history[pl2][current_round-1] == 2 ) ))
                        SUB_PENALTY(pflot, opposite_float_pen);
                }
#else

                /* l'ancien code de Thierry :
                 *
                 * Retrancher les minorations (flottement anti-cumule)
                 */

                if (pl1 >= 0 && f * last_float[pl1] < 0)
                    SUB_PENALTY(pflot, opposite_float_pen);
                if (pl2 >= 0 && f * last_float[pl2] > 0)
                    SUB_PENALTY(pflot, opposite_float_pen);

#endif


                /*
                 * Ajouter la penalite d'elitisme - elitism penalty
                 */
#ifdef ELITISM
                if ((elitism_penalty[current_round] != 0) && (f != 0) &&
                  (sci >= 0) && (scj >= 0)) {
                    pen_t  valeur_elitisme;

                    valeur_elitisme = (elitism_penalty[current_round] * (sci+scj) * labs(f)) / 2;
                    assert( valeur_elitisme >= 0); /* pas de gag d'overflow ! - no overflow gag! */
                    ADD_PENALTY(pflot, valeur_elitisme);
                }
#endif

                /* La penalite de flottement est maintenant dans pflot - float penalty is now in pflot */
                assert(pflot >= 0);
                ADD_PENALTY(m[i][j], pflot);
                ADD_PENALTY(m[j][i], pflot);
            }
    /*
     * Penalites de chauvinisme - country penalty
     */
    if (!papp_tournament_api) {
        printf(PENA_COUNTRY);
        fflush(stdout);
    }
    for (i = 0; i < NumberPlayers; i++)
        for (j = 0; j < NumberPlayers; j++)
            if (i < j) {
                Player *j1, *j2;

                j1 = findPlayer(tbl[i]);
                j2 = findPlayer(tbl[j]);
                if (!compare_strings_insensitive(j1->country, j2->country)) {
                    ADD_PENALTY(m[i][j], country_penalty[current_round]);
                    ADD_PENALTY(m[j][i], country_penalty[current_round]);
                }
            }
    /*
     * Le calcul des penalites est termine, nous pouvons maintenant appeler la fonction d'optimisation
     *****
     * Penalties computation is now over, optimisation function can now be called
     */
    if (!papp_tournament_api) {
        printf(".\n");
        fflush(stdout);
    }

    if (!papp_tournament_api) printf(OPTIMAL_PAIR "\n");
    result = doPairings(EffectivePlayersNumber / 2, m, v);
    /* Apparier seulement les vrais joueurs - pair only real players */
#ifdef DEBUG_PEN
    printf("\n");
#endif
    for (i=0; i<EffectivePlayersNumber; i+=2) {
        pl1 = tbl[v[i]];
        pl2 = tbl[v[i+1]];
        pl1_is_Black = 1;
        if (pl1 >= 0 && pl2 >= 0) {
            assert(inscription_ID(pl1) >= 0);
            assert(inscription_ID(pl2) >= 0);
            /* Derniere phase: optimisation chromatique - last phase, chromatic optimisation */
            if (m[v[i]][v[i+1]] != m[v[i+1]][v[i]]) {
                make_couple(pl1, pl2, UNKNOWN_SCORE);
                pl1_is_Black = 1;
            } else {
                long f = chromatic_optimisation(pl1, pl2);
                if (f == 1) {
                    make_couple(pl1, pl2, UNKNOWN_SCORE);
                    pl1_is_Black = 1;
                } else if (f == 2) {
                    make_couple(pl2, pl1, UNKNOWN_SCORE);
                    pl1_is_Black = 0;
                } else {
                /* Les couleurs sont indifferentes - colors are indifferent */
                    if (getRandom(2)) {
                        make_couple(pl1, pl2, UNKNOWN_SCORE);
                        pl1_is_Black = 1;
                    } else {
                        make_couple(pl2, pl1, UNKNOWN_SCORE);
                        pl1_is_Black = 0;
                    }
                }
            }
        }

#ifdef DEBUG_PEN
        if (!pl1_is_Black) {
            n = pl1;
            pl1 = pl2;
            pl2 = n;
        }
        printf("%-8.8s(%6ld)   %-8.8s(%6ld)%10ld\n",
           ((pl1>=0) ? trouver_joueur(pl1)->fullname : BYE_NAME),
           pl1,
           ((pl2>=0) ? trouver_joueur(pl2)->fullname : BYE_NAME),
           pl2,
           (long) m[v[i]][v[i+1]]);
/*  printf("%ld\t%ld\t%8ld\n", pl1, pl2, (long) m[v[i]][v[i+1]] ); */
#endif

    }
#ifdef DEBUG_PEN
    printf("\nTotal\t\t%10ld\n\n", (long)result);
    printf(HIT_ANY_KEY);
    (void)lire_touche();
#endif
    /* Liberation de la memoire - freeing memory */
    free(v);
    free(last_color);
    free(color_diff);

#ifdef COMPENSATION_FLOATS
    free(wins_history_buffer);
    free(wins_history);
#endif
}



/*
 * chromatic_optimisation(n1,n2): n1 et n2 sont les deux numeros Elo des joueurs a apparier.
 * Cette fonction renvoie 1 s'il vaut mieux que n1 joue les noirs, 2 s'il vaut mieux que n2 joue les noirs,
 * et 0 si la couleur est indifferente.
 *
 ****
 *
 * chromatic_optimisation(n1,n2): n1 and n2 are two ELO numbers of paired players.
 * This function returns 1 if it's better if n1 plays black, 2 if it's better for n2 to play black
 * and 0 is color doesn't matter.
 */

static long chromatic_optimisation (long n1, long n2) {
    long round0 = current_round, p1, p2;

    while (--round0 >= 0) {
        p1 = polar2(n1, round0);
        p2 = polar2(n2, round0);
        if (p1 == p2)
            continue;
        /* C'est la ronde la plus recente ou les couleurs sont differentes -
         * most recent round where colors are different*/
        if (p1 == 1 || p2 == 2)
            return 2;
        if (p1 == 2 || p2 == 1)
            return 1;
        /* Nous ne devrions jamais arriver ici - we should never arrive here */
        assert(0);
    }
    /* Les joueurs ont toujours eu les memes couleurs - Players always had same colors */
    return 0;
}
