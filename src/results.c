/*
 * results.c: entree des resultats
 *
 * (EL) 27/10/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, ajout dans 'saveStandingsFile()' et dans 'sauver_fichier_resultats()'
 *                   du test pour decider de la sauvegarde au format xml.
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : changement des 'fopen()' en 'myfopen_in_subfolder()'
 * (EL) 02/02/2007 : 'calcul_departage()' utilise maintenant un tableau
 *                   'tieBreak[]' compose de doubles.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 *****
 *
 * results.c: entering results
 *
 * (EL) 27/10/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, Add, in 'saveStandingsFile()' and in 'saveResultsFile()'
 *                   a test to decide whether saving with xml format.
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : change all 'fopen()' to 'myfopen_in_subfolder()'
 * (EL) 02/02/2007 : 'tieBreak_computation()' now uses a 'tieBreak[]' array made of doubles.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
  */

#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <ctype.h>
#include <string.h>
#include <math.h>
#include "player.h"
#include "couplage.h"
#include "global.h"
#include "more.h"
#include "tiebreak.h"
#include "crosstable.h"

#ifdef ENGLISH
# define ERES_COMPLETE  "THE RESULTS ARE COMPLETE: type '!' to confirm."
# define ERES_PROMPT    "Player & Score: "
# define ERES_RANDOM    "Choosing random results..."
# define ERES_I_PAIR    "Incomplete pairings!"
# define ERES_I_RES     "Incomplete results!"
# define ERES_I_SCORE   "Maximum score is %ld"
# define ERES_I_EVEN    "The relative score should be even\n"
# define ERES_I_ODD     "The relative score should be odd\n"
# define ERES_I_PLAYER  "Player %ld didn't play this round"
# define ERES_I_AGAIN   "This coupon has already been entered\n"
# define ERES_I_CLOBBER "Previous score was %s\n"
# define FICH_HEADER    "Feats of player %s (%ld) after round %ld:"
# define FICH_TRAILER_IMPAIR    "Total of %ld.5 points out of %ld, with %s discs."
# define FICH_TRAILER_PAIR      "Total of %ld points out of %ld, with %s discs."
# define FICH_TRAILER   "Total of %ld half-points out of %ld, with %s discs."
# define FICH_OUT       "The player has left the tournament."
# define CORR_PROMPT    "Correction in which round (%ld-%ld)? "
# define CORR_ROUND     "Round %ld"
# define CORR_BLACK     "Real first player (%s) was:"
# define CORR_WHITE     "Real second player (%s) was:"
# define CORR_SCORE_BLACK    "Real score for %s was: "
# define CORR_SCORE_WHITE    "Real score for %s was: "
# define CORR_POSITIVE  "Number of discs must be positive!"
# define CORR_SOMME     "Sum of discs must be equal to %ld !"
# define CORR_DISCS     "%s discs"
# define CORR_PAS_JOUE  "These players didn't play together at round %ld !"
# define CORR_OK        "Correction accepted (round %ld):"
# define CORR_REGENERATE    "\n\nRegenerate file \"%s\" (Y/N) ? "
# define CORR_ERROR     "CORRECTION IS CANCELED, "
# define CORR_TOUCHE    "hit any key..."
# define MSG_CROSS_MEMORY "Not enough memory to generate the cross-table."
#else
# define ERES_COMPLETE  "RESULTATS COMPLETS: tapez '!' pour confirmer."
# define ERES_PROMPT    "Joueur & Score: "
# define ERES_RANDOM    "Tirage des resultats au hasard..."
# define ERES_I_PAIR    "Appariements incomplets !"
# define ERES_I_RES     "Resultats incomplets !"
# define ERES_I_SCORE   "Le score ne peut depasser %ld"
# define ERES_I_EVEN    "La difference de pions doit etre paire\n"
# define ERES_I_ODD     "La difference de pions doit etre impaire\n"
# define ERES_I_PLAYER  "Le joueur %ld n'a pas joue a cette ronde"
# define ERES_I_AGAIN   "Coupon deja entre\n"
# define ERES_I_CLOBBER "Le precedent score etait %s\n"
# define FICH_HEADER    "Fiche du joueur %s (%ld) apres la ronde %ld:"
# define FICH_TRAILER_IMPAIR    "Soit au total %ld.5 points sur %ld, avec %s pions."
# define FICH_TRAILER_PAIR      "Soit au total %ld points sur %ld, avec %s pions."
# define FICH_TRAILER   "Soit au total %ld demi-points sur %ld, avec %s pions."
# define FICH_OUT       "Le joueur est sorti du tournoi."
# define CORR_PROMPT    "Correction dans quelle ronde (%ld-%ld) ? "
# define CORR_ROUND     "Ronde %ld"
# define CORR_BLACK     "Premier joueur (%s) reel :"
# define CORR_WHITE     "Deuxieme joueur (%s) reel :"
# define CORR_SCORE_BLACK    "Score reel de %s : "
# define CORR_SCORE_WHITE    "Score reel de %s : "
# define CORR_POSITIVE  "Le nombre de pions doit etre positif !"
# define CORR_SOMME     "La somme des pions doit faire %ld !"
# define CORR_DISCS     "%s pions"
# define CORR_PAS_JOUE  "Ces joueurs n'ont pas joue ensemble a la ronde %ld !"
# define CORR_OK        "Correction acceptee (ronde %ld) :"
# define CORR_REGENERATE    "\n\nRegenerer le fichier \"%s\" (O/N) ? "
# define CORR_ERROR     "CORRECTION ANNULEE, "
# define CORR_TOUCHE    "pressez une touche..."
# define MSG_CROSS_MEMORY "Pas assez de memoire pour la cross-table."
#endif

static long areResultsComplete(void) {
    long n1, n2;
    discs_t v;

    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v))
        if (COUPON_IS_EMPTY(v)) return 0;
    return 1;
}

/*  sauvegardes dans les fichiers "stand###.txt" - saves in "stand###.txt" files */
void saveStandingsFile(void) {
    char    *filename, old_more_mode[10];
    FILE    *file;
    long     i;

    if (standings_file_save) {

        filename = numbered_filename(standings_filename, current_round);

        assert(standings_filename != NULL );
        assert(results_filename != NULL);
        assert(pairings_filename != NULL);

        /* Si deux noms de fichiers se telescopent, il faut ajouter
            le classement a la fin du fichier avec le mode d'ecriture "append"
         ****
         * If two filenames are the same, standings must be added at the end of the file
         * with the "append" opening mode.
         */
        if ((result_file_save &&
             (strcmp(results_filename, standings_filename) == 0 )) ||
            (pairings_file_save &&
             (strcmp(pairings_filename, standings_filename) == 0 ))) {

            file = myfopen_in_subfolder(filename, "a", subfolder_name, use_subfolder, 1);
            fprintf(file,"\n\n\n");
            fclose(file);

            strcpy(old_more_mode,more_get_mode());
            more_set_mode("a");
            display_registered(filename);
            more_set_mode(old_more_mode);
        } else
            display_registered(filename);

        if (automatic_printing)
            for (i = 0; i < print_copies; i++)
                print_file(filename);
    }

	/* Faut-il generer le fichier XML du classement ? - Does the xml standings file have to be created? */
	if (generate_xml_files) {
        createXMLstandings();
	}

}

/*  sauvegardes dans les fichiers "team###.txt" - saves in "team###.txt" files */
void saveTeamsFile(void) {
    char    *filename, old_more_mode[10];
    FILE    *file;
    long     i;

    if (team_standings_file_save) {

        filename = numbered_filename(team_standings_filename, current_round);

        assert(standings_filename != NULL );
        assert(results_filename != NULL);
        assert(pairings_filename != NULL);
        assert(team_standings_filename != NULL);

        /* Si deux noms de fichiers se telescopent, il faut ajouter
           le classement a la fin du fichier avec le mode d'ecriture "append"
         *****
         * If two filenames are the same, standings must be added at the end of the file
         * with the "append" opening mode.
         */
        if ((result_file_save &&
            (strcmp(results_filename, team_standings_filename) == 0 )) ||
            (pairings_file_save &&
            (strcmp(pairings_filename, team_standings_filename) == 0 )) ||
            (standings_file_save &&
            (strcmp(standings_filename, team_standings_filename) == 0 ))) {

            file = myfopen_in_subfolder(filename, "a", subfolder_name, use_subfolder, 1);
            fprintf(file,"\n\n\n");
            fclose(file);

            strcpy(old_more_mode,more_get_mode());
            more_set_mode("a");
            display_teams(filename);
            more_set_mode(old_more_mode);
        } else
            display_teams(filename);

        if (automatic_printing)
            for (i = 0; i < print_copies; i++)
                print_file(filename);
    }
}

/*  sauvegardes dans les fichiers "cross###.htm" - saves in "cross###.txt" files */
void saveHTMLcrosstableFile(void) {

    if (html_crosstable_file_save && (current_round >= 1)) {

        if (!CanAllocateCrosstableMemory()) {
            clearScreen();
            printf(MSG_CROSS_MEMORY);
            read_key();
        } else {
            PrepareCrosstableCalculations(current_round - 1);

            assert(HTML_crosstable_filename != NULL );
            if (strstr(HTML_crosstable_filename, "###"))
                HTMLCrosstableSave(numbered_filename(HTML_crosstable_filename, current_round));
            else
                HTMLCrosstableSave(HTML_crosstable_filename);

            FreeCrosstableMemory();
        }
    }
}

/* affichage des coupons lors de l'entree des resultats :
 * on affiche d'abord les coupons remplis, puis les vides.
 *
 *****
 *
 * displaying coupons when entering results: first filled ones then empty ones
 */
void displayEnterGameResults(void) {
    long i, n1, n2;
    Pair *list;
    long numberPairs;
    discs_t v;

	/* Une ligne "vide" pour faire plaisir à Marc - empty line because Marc asked for it */
	puts("");

    /* D'abord ceux qui sont remplis... - First filled coupons */
    CALLOC(list, 1 + registered_players->n / 2, Pair);
    numberPairs = 0;
    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v))
        if (COUPON_IS_FILLED(v)) {
        i = numberPairs++;
        list[i].j1 = findPlayer(n1);
        list[i].j2 = findPlayer(n2);
        list[i].score = v;
        list[i].sort_value = tables_sort_criteria(n1, n2);
    }
    if (current_round >= 1)
        SORT(list, numberPairs, sizeof(Pair), pairs_sort);
    for (i = 0; i < numberPairs; i++)
        puts(coupon(list[i].j1, list[i].j2, list[i].score));
    free(list);

    /* ... puis les vides. - then empty ones */
    CALLOC(list, 1 + registered_players->n / 2, Pair);
    numberPairs = 0;
    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v))
        if (COUPON_IS_EMPTY(v)) {
        i = numberPairs++;
        list[i].j1 = findPlayer(n1);
        list[i].j2 = findPlayer(n2);
        list[i].score = v;
        list[i].sort_value = tables_sort_criteria(n1, n2);
    }
    if (current_round >= 1)
        SORT(list, numberPairs, sizeof(Pair), pairs_sort);
    for (i = 0; i < numberPairs; i++)
        puts(coupon(list[i].j1, list[i].j2, list[i].score));
    free(list);
}

/* fonction interactive d'entree des resultats - interactive function to enter the results */
void enterResults(void) {
    long displayMessage = 1, ret, n1, n2, len;
    discs_t v;
    long player, relative ;
    discs_t score;
    char buffer[BUFFER_SIZE+1], tmp[BUFFER_SIZE+1];
    Player *pl1, *pl2;

    /* Y a-t-il des resultats a rentrer, vraiment ? - Are there really results to be entered */
    round_iterate(current_round);
    if (next_couple(&n1, &n2, &v) == 0)
        return;
    clearScreen();
    while (1) {
        if (displayMessage && areResultsComplete() && nbr_unpaired_players()<2) {
            /* oui, ils sont complets, et les appariements aussi
             ****
             * yes, they are completed and so are the pairings */
            displayEnterGameResults();
            puts(ERES_COMPLETE);
            displayMessage = 0;
        }
        /* Effacement du tampon d'entree - clear input buffer */
        buffer[0] = '\0';
prompt:
        printf(ERES_PROMPT);
        ret = read_Line_init(buffer, BUFFER_SIZE, 1);
        putchar('\n');

        len = strlen(buffer);
        if ((len > 0) && (removeLeftSpaces(buffer) == len)) {
            /* Que des espaces ! - only spaces! */
            beep();
            goto prompt;
        }

        if (ret < 0 || (ret == 0 && buffer[0] == 0)) {
            /* Quittons ce sous-menu - let's quit the submenu */
            return;
        }
        if (ret > 0) {
            /*
             * L'utilisateur hesite (il a tape sur Tab), affichons-lui la liste triee des coupons.
             *****
             * User hesitates (he pressed Tab), a sorted list of coupons will be displayed
             */
            displayEnterGameResults();

            /* Et revenir au prompt, sans effacer le tampon - come back to prompt without clearing buffer */
            goto prompt;
        }

        /* Deux cas particuliers : '!' et '?' - two special cases: '!' and '?' */
        if (strcmp(buffer, "?") == 0) {
            puts(ERES_RANDOM);
            imagineResults();
            continue;
        }
        if (strcmp(buffer, "!") == 0) {
            if (nbr_unpaired_players() > 1) {
                /* appariements incomplets - incomplete pairings */
                puts(ERES_I_PAIR);
                beep();
                continue;
            }
            if (!areResultsComplete()) {
                /* resultats incomplets - incomplete results */
                puts(ERES_I_RES);
                beep();
                continue;
            }

            /* Tout a l'air bon, hein? Valider les resultats et les sauvegarder
             ****
             * Everything looks ok, validate results and save them */
            resultsValidation(result_file_save);
            /* Sauvegarder le nouveau classement individuel et par equipes
             *****
             * Save the new individual and team standings */
            saveStandingsFile();
            saveTeamsFile();
            saveHTMLcrosstableFile();
            /* Afficher le nouveau classement - display new standings */
            display_registered(NULL);
            return;
        }
        /*
         * Exemples d'entrees autorisees (tous les scores peuvent etre des flottants) :
         ****
         * Example of authorized inputs (all scores can be floats):
         *
         * 145 35              le joueur 145 fait 35 pions - player 145 has 35 discs
         * 145 +6              il gagne de 6 pions - he wins by 6 discs
         * 145 -8              il perd de 8 pions - he losses by 8 discs
         * 145 =               il fait nulle - he draws
         * 145 +0              idem - same
         * 145 -0              idem - same
         * 145 =0              idem - same
         * 145 x               effacer son coupon - clears his coupon
         * 145 e               idem - same
         * -145                idem - same
         * POIRIER Serge 35    le fullname du joueur en toutes lettres - player's fullname
         * poirier +6          maj/min indifferentes - upper/lower case indifferent
         * poi -8              la machine complemente automatiquement - computer automatically completes
         * poi35               et le score peut etre colle - score can by attached
         * poirier x           ne marche pas : la machine croit que x est une initiale de prenom
         *                     doesn't work : computer thinks x is a first name initial
         * poirier e           non plus - same
         * -poi                ca, ca marche - this is ok
         */

        if (buffer[0] == '-') {
            if (numberCompletionInCoupons(buffer + 1, &player, tmp) != 1)
                player = atoi(buffer+1);
            score = UNKNOWN_SCORE;
            relative = 0;
        } else {
            if ((numberCompletionInCoupons(buffer, &player, tmp) == 1) ||
                (sscanf(buffer,"%ld%s", &player, tmp) == 2)) {
                switch (tmp[0]) {
                  case '\0'  :
                    beep(); continue;
                  case 'x':
                  case 'e':
                    score = UNKNOWN_SCORE; relative = 0; break;
                  case '=':
                  case '+':
                  case '-':
                  default:
                    if (understandScore(tmp, &score, &relative)) break;
                    else { beep(); continue; }
                }
            } else {
             /* Erreur de syntaxe - syntax error */
                beep();
                continue;
            }
        }

        /* Verifier le score - check score */
        if (SCORE_TOO_LARGE(score) ||
           (relative && !SCORE_IS_POSITIVE(score))) {
            printf(ERES_I_SCORE "\n", discsTotal);
            beep();
            continue;
        }


        /* On n'autorise pas les scores relatifs impairs.
         *  Ceci empeche de rentrer des demi-pions en relatif...
         *****
         * No odd relative scores. It prevents entering half-points in relative
         */
#if (!defined(USE_HALF_DISCS))

        if (relative && ((score+discsTotal)%2 != 0)) {
            if (discsTotal%2) printf(ERES_I_ODD);
            else printf(ERES_I_EVEN);
            beep(); continue;
        }

#endif

        /* Chercher le coupon contenant le joueur - look for the player's coupon */
        round_iterate(current_round);
        while ((ret = next_couple(&n1, &n2, &v)) != 0) {
            if (n1 == player)
                break;
            if (n2 == player) {
                if (COUPON_IS_FILLED(score))
                    score = OPPONENT_SCORE(score);
                break;
            }
        }
        if (ret == 0) {
            printf(ERES_I_PLAYER "\n", player);
            beep();
            continue;
        }
        /* Le coupon contenait-il deja un score? - Was there already a score? */
        if (COUPON_IS_FILLED(v) && SCORE_IS_POSITIVE(score))
            printf(SCORES_EQUALITY(score,v) ? ERES_I_AGAIN : ERES_I_CLOBBER, fscore(v));

        *couple_value(n1, n2) = score;

        /* Puis afficher le scoupon apres modification - display coupon after changes */
        pl1 = findPlayer(n1);
        pl2 = findPlayer(n2);
        puts(coupon(pl1, pl2, score));
    }
}

void imagineResults(void) {
    long n1, n2, grade1, grade2, half0, half1;
    discs_t *valc, scn, v;
    double prob_n;

    /* Petite et grande moitie du nombre de pions  - small and large halfs of number of discs */
    half0 = discsTotal / 2;
    half1 = (discsTotal + 1) / 2;
    assert(!BAD_TOTAL(INTEGER_TO_SCORE(half0), INTEGER_TO_SCORE(half1)));

    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v)) {
        if (COUPON_IS_FILLED(v))
            continue;
        grade1 = findPlayer(n1)->rating;
        grade2 = findPlayer(n2)->rating;
        if (grade1 < 1100)  grade1 = 1100;
        if (grade2 < 1100)  grade2 = 1100;
        /*
         * La probabilite que Noir gagne vaut - Black win probability is
         *
         *            1
         *  -----------------------
         *        (grade2-grade1)/200
         *  1 + 3
         *
         */
        prob_n = 1.0 / (1.0 + pow(3.0, (grade2-grade1)/200.0));

        if (getRandom(1000)/1000.0 < prob_n)
            scn = INTEGER_TO_SCORE(half1 + getRandom(half0 + 1));  /* Noir  gagne - Black win */
        else
            scn = INTEGER_TO_SCORE(half0 - getRandom(half0 + 1));  /* Blanc gagne - White win */
        valc = couple_value(n1, n2);
        assert(valc && SCORE_IS_LEGAL(scn));
        *valc = scn;
    }
}

void clearScores(void) {
    long i;

    for (i=0; i<MAX_REGISTERED ; i++) {
        present[i] = 0;
        score[i] = 0;
        nbr_discs[i]  = ZERO_DISC;
        tieBreak[i] = 0.0;
        last_float[i] = 0;
        team_score[i] = 0;
    }
}

void updateScores(void) {
    long i, n1, n2, f, discsAgainstBye, eloID;
    discs_t v;

    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v)) {
        assert(SCORE_IS_LEGAL(v));
        n1 = inscription_ID(n1);
        n2 = inscription_ID(n2);
        assert(n1 >= 0 && n2 >= 0);
        assert(present[n1] && present[n2]);
        ADD_SCORE(nbr_discs[n1], v);
        ADD_SCORE(nbr_discs[n2], OPPONENT_SCORE(v));
        /* Calcul du flottement de la ronde precedente - compute floats from previous round */
        f = score[n1] - score[n2];
        last_float[n1] = f;
        last_float[n2] = -f;
        /* Calcul des nouveaux scores - compute new scores */
        if (IS_DEFEAT(v))         /* Blanc gagne - White wins */
            score[n2] += 2;
        else if (IS_VICTORY(v))   /* Noir gagne  - Black wins */
            score[n1] += 2;
        else                            /* Match  nul - Draw */
            score[n1]++,score[n2]++;
    }
    /* Mettre a jour ceux qui ont joue contre bip - Update players paired against Bye */
    if (IS_DEFEAT(bye_score))
        discsAgainstBye = 2;
    else if (IS_VICTORY(bye_score))
        discsAgainstBye = 0;
    else
        discsAgainstBye = 1;
    for (i=0; i<registered_players->n; i++) {
        eloID = registered_players->list[i]->ID;
        if (present[i] && polarity(eloID)==0) {
            ADD_SCORE(nbr_discs[i], OPPONENT_SCORE(bye_score));
            score[i] += discsAgainstBye;
            last_float[i] = discsAgainstBye - 1;     /* La valeur exacte importe peu - real value doesn't matter */
        }
    }
}

/*  sauvegardes dans les fichiers "resul###.txt" - saves in "resul###.txt" files */
void saveResultsFile(void) {
    char    *filename,  old_more_mode[10];
    FILE    *file;
    long     i;

    if (result_file_save)  {
        filename = numbered_filename(results_filename, current_round + 1);

        /* si les noms de fichiers sont les memes, utiliser le mode d'ecriture "append"
         *****
         * If two filenames are the same, the "append" opening mode is used.
         */
        if (pairings_file_save &&
           (strcmp(pairings_filename, results_filename) == 0 )) {
            file = myfopen_in_subfolder(filename, "a", subfolder_name, use_subfolder, 1);
            fprintf(file,"\n\n\n");
            fclose(file);

            strcpy(old_more_mode,more_get_mode());
            more_set_mode("a");
            display_pairings(filename, 1);
            more_set_mode(old_more_mode);
        } else
            display_pairings(filename, 1);

        if (automatic_printing)
          if (!standings_file_save ||
               (strcmp(standings_filename, results_filename) != 0 ))
              for (i = 0; i < print_copies; i++)
                  print_file(filename);
    }

    /* Faut-il generer le fichier XML du classement ? - Does the xml standings file have to be created? */
	if (generate_xml_files) {
        createXMLround();
	}
}

void resultsValidation(long isSaveResultsFile) {

    /* Creer, si ce n'est deja fait, les numeros de table en memoire (mieux vaut tard que jamais !)
     *****
     * Create (if not already done), table numbers in memory
     * */
    tables_numbering();
    /* Sauvegarder les resultats de la ronde courante dans le fichier intermediaire
     *****
     * Save current round results in intermediate file
     */
    save_registered();
    save_round();
    /* Sauvegarder les resultats en clair ? - Save results? */
    if (isSaveResultsFile)
        saveResultsFile();

    updateScores();
    next_round();
}

/*
 * Correction d'un resultat dans une ronde passee ou dans la ronde presente si on a deja rentre des resultats.
 * On peut intervertir les joueurs et/ou changer le score.
 *****
 * Correction of a previous results in a previous round or the current one if results have already been entered.
 * Players can be exchanged and/or score can be changed
 */
 void ResultCorrection(void) {
    char     string[256], *line;
    long     round2correct, ret, relative;
    long     blackCorrect, whiteCorrect;
    discs_t  blackScoreCorrect, whiteScoreCorrect;
    long     minRound, maxRound;


    assert( current_round >= 0 && current_round < NMAX_ROUNDS);
    minRound = 0;
    maxRound = couples_nbr(current_round) ? current_round : current_round - 1;
    if (minRound > maxRound) return;

correction :

    clearScreen();

    /* entree du ID de la ronde a modifier - which round number to correct? */
    assert(minRound <= maxRound);
    printf(CORR_PROMPT, minRound + 1, maxRound + 1);
    if (sscanf(read_Line(), "%ld", &round2correct) != 1)
      return;
    round2correct = round2correct - 1;

    if ((round2correct < minRound) || (round2correct > maxRound))
      return;
    assert( round2correct >= minRound && round2correct <= maxRound);
    printf("\n  " CORR_ROUND, round2correct + 1);

    /* entree du bon joueur noir - correct black player */
    printf("\n");
    sprintf(string, CORR_BLACK, color_1);
    blackCorrect = selectPlayerFromKeyboard(string, registered_players, &line);
    if (blackCorrect <= 0)
        goto abandon;
    printf(" %s\n",coupon(findPlayer(blackCorrect), NULL, UNKNOWN_SCORE));

    /* entree du bon score noir - correct black score */
    printf("" CORR_SCORE_BLACK, color_1);
    if ( !understandScore(read_Line(), &blackScoreCorrect, &relative) )
        goto abandon;
    if (!SCORE_IS_POSITIVE(blackScoreCorrect)) {
        printf("\n\n" CORR_POSITIVE "\n");
        goto abandon;
    }
    if (SCORE_TOO_LARGE(blackScoreCorrect)) {
        printf("\n\n" ERES_I_SCORE "\n", discsTotal);
        goto abandon;
    }
    printf("\n  " CORR_DISCS, discs2string(blackScoreCorrect));

    /* entree du bon joueur blanc - correct white player */
    printf("\n");
    sprintf(string, CORR_WHITE, color_2);
    whiteCorrect = selectPlayerFromKeyboard(string, registered_players, &line);
    if (whiteCorrect <= 0) goto abandon;
    printf(" %s\n",coupon(findPlayer(whiteCorrect), NULL, UNKNOWN_SCORE));

    /* entree du bon score blanc - correct white score */
    printf("" CORR_SCORE_WHITE, color_2);
    if ( !understandScore(read_Line(), &whiteScoreCorrect, &relative) )
        goto abandon;
    if (!SCORE_IS_POSITIVE(whiteScoreCorrect)) {
        printf("\n\n" CORR_POSITIVE "\n");
        goto abandon;
    }
    if (SCORE_TOO_LARGE(whiteScoreCorrect)) {
        printf("\n\n" ERES_I_SCORE "\n", discsTotal);
        goto abandon;
    }
    printf("\n  " CORR_DISCS, discs2string(whiteScoreCorrect));

    /* verification de la somme des pions - checking total number of discs */
    if (BAD_TOTAL(blackScoreCorrect,whiteScoreCorrect)) {
        printf("\n\n" CORR_SOMME "\n", discsTotal);
        goto abandon;
    }

    assert( !BAD_TOTAL(blackScoreCorrect,whiteScoreCorrect) );

    switch (change_result(round2correct, blackCorrect, whiteCorrect, blackScoreCorrect)) {
    case 1 :
        /* OK, on affiche le coupon corrige - OK, displaying corrected coupon */
        printf("\n\n" CORR_OK "\n",round2correct + 1);
        puts(coupon(findPlayer(blackCorrect), findPlayer(whiteCorrect), blackScoreCorrect));
        /* on regenere le fichier intermediaire - regenerating intermediate workfile */
            recreate_workfile();
        /* on le relit immediatement pour recalculer les scores - read it again to compute scores */
            first_round();
        ret = read_file(workfile_filename, CONFIG_F);
        if (ret > 0)
            fatal_error("ERREUR DE FICHIER dans la fonction ResultCorrection");

        /* demander si on veut sauver le nouveau classement - ask if new standings have to be saved */
        if ((round2correct < current_round) &&
            (standings_file_save ||
             team_standings_file_save ||
             html_crosstable_file_save))   {
            sprintf(string, CORR_REGENERATE,
                numbered_filename(standings_filename, current_round));
            if (yes_no(string))    {
                saveStandingsFile();
                saveTeamsFile();
                saveHTMLcrosstableFile();
                }
            return;
        }

        /* Afficher les fiches individuelles des joueurs concernes - display individual information about the players */
        /* fiche_individuelle(blackCorrect, NULL);
           individualSheet(whiteCorrect,NULL); */

        printf("\n\n");
        goto press_key;
    case 0 : /* pas de changement - no change */
        printf("\n\n" ERES_I_AGAIN);
        goto abandon;
    case -1 : /* les joueurs specifies n'ont pas joue ensemble ! - specified players haven't played together */
        printf("\n\n" CORR_PAS_JOUE "\n", round2correct + 1);
        goto abandon;
    }


abandon :
    beep();
    printf( "\n\n" CORR_ERROR);
press_key :
    printf(CORR_TOUCHE);
    if (tolower(read_key()) == 'c')
      goto correction;
    clearScreen();
    return;
}

/*
 * Affichage de la fiche d'un joueur. La somme des points et des pions peut ne pas correspondre
 * aux totaux indiques si le joueur a joue contre bip
 *****
 * Display a player information sheet. Points and discs may not correspond to the displayed totals
 * if the payer was paired against bye.
 */
 void individualSheet(long playerNumber, const char *filename) {
    long     i, round0, color, opponent, maxColorChars;
    discs_t Score;
    Player  *pl;
    char    *name, line[256], *comm, csc[256];

    maxColorChars = strlen(color_1);
    if ((i = strlen(color_2)) > maxColorChars)
        maxColorChars = i;

    i = inscription_ID(playerNumber);
    if (i < 0) {
        /* Le joueur n'est pas inscrit - the player is not registered */
        beep();
        return;
    }
    name = findPlayer(playerNumber) -> fullname;

    more_init(filename);
    sprintf(line, FICH_HEADER, name, playerNumber, current_round);
    more_line(line); more_line("");
    if (current_round < 1)
        goto end_sheet;

    /*
     * Toute cette partie (jusqu'a end_sheet) est sautee si nous sommes avant la premiere ronde.
     *****
     * All this part (up to end_sheet) is avoided if we're before the first round.
     */
    for (round0 = 0; round0 < current_round; round0++)
        if ((color = polar2(playerNumber,round0)) != 0) {
            /* Chercher l'adversaire - look up the opponent */
            long n1, n2;
            discs_t v;

            opponent = -1;
            Score = UNKNOWN_SCORE;
            round_iterate(round0);
            while (next_couple(&n1, &n2, &v)) {
                if (n1 == playerNumber) {
                    assert(color == 1);
                    opponent = n2;
                    Score = v;
                    break;
                } else if (n2 == playerNumber) {
                    assert(color == 2);
                    opponent = n1;
                    Score = OPPONENT_SCORE(v);
                    break;
                }
            }
            /* L'avons-nous trouve ? - Was he found? */
            assert(opponent >= 0 && SCORE_IS_LEGAL(Score));
            pl = findPlayer(opponent);
            assert(pl);
            if (display_score_diff) {
                strcpy(csc, fscore(Score));
            } else {
                if (IS_VICTORY(Score))
                    sprintf(csc, "%c %s", '+', discs2string(Score));
                if (IS_DEFEAT(Score))
                    sprintf(csc, "%c %s", ' ', discs2string(Score));
                if (IS_DRAW(Score))
                    sprintf(csc, "%c %s", '=', discs2string(Score));
            }
            sprintf(line,"%3ld. %-*s  %-*s  %s (%ld)", round0+1,
            (int)maxColorChars, (color == 1 ? color_1 : color_2), (int)scores_digits_number + 4 ,csc,
            pl->fullname, pl->ID);
            more_line(line);
        } else {
            /* Le joueur a passe, indiquons-le clairement: - player didn't play */
            sprintf(line,"%3ld.", round0+1);
            more_line(line);
        }
    /* Afficher le total des points, des pions et des scores - Display points, discs and scores */
    more_line("");
    /* La variable i contient toujours le ID d'inscription - i has registration ID */
    if (display_score_diff) {
        /*
         * On implemente la ligne suivante : - following line is implemented
         * Score = 2*nbr_discs[i] - round*discsTotal;
         */
        Score = nbr_discs[i];
        ADD_SCORE(Score, Score);
        ADD_SCORE(Score, INTEGER_TO_SCORE(-current_round * discsTotal));
        sprintf(csc, "%s%s", SCORE_IS_POSITIVE(Score) ? "+":"", discs2string(Score));
    } else {
        sprintf(csc, "%s", discs2string(nbr_discs[i]));
    }

    if ((score[i] % 2) == 0)
        sprintf(line, FICH_TRAILER_PAIR, score[i] / 2, current_round, csc);
    else
        sprintf(line, FICH_TRAILER_IMPAIR, score[i] / 2, current_round, csc);

    more_line(line);

end_sheet:
    /*
     * Meme si aucune partie n'a encore ete jouee, il peut etre interessant de savoir
     * si le joueur a deja(!) abandonne, ou s'il y a un commentaire le concernant.
     *****
     * Even if no games have been played, it may be interesting to know if the player
     * has already resigned or if there is any commentary about him.
     */
    if (!present[i])
        more_line(FICH_OUT);
    comm = findPlayer(playerNumber) -> comment;
    if (comm) {
        sprintf(line, "--> %s", comm);
        more_line(line);
    }
    more_close();
}

/*
 * Calcul du departage.
 *
 * On recupere tous les resultats et on appelle la fonction PlayerTiebreak() du fichier tiebreak.c
 *****
 * Tie-break computation
 *
 * All results are gathered and PlayerTiebreak() function from tiebreak.c is called
 */
void tieBreak_computation (void) {
    long i, round0, n1, n2, n, eloID;
    BQ_results TabPL ;
    discs_t v, discs;
    long SO ;
    long numberPlayers = registered_players->n;

    /* Initialisation - Initialization */
    for (i = 0; i < numberPlayers; i++)
        tieBreak[i]  = 0.0;

    if (current_round <= 0)
        return;

    /* Calcul du departage de chaque joueur - Compute each player tie break */
    for (i = 0; i < numberPlayers; i++) {
        for (round0 = 0; round0 < current_round; round0++) {
            TabPL.opp[round0] = 0 ;
            TabPL.opp_points[round0] = 0 ;
            TabPL.score[round0] = UNKNOWN_SCORE ;
            round_iterate(round0);
            while (next_couple(&n1, &n2, &v)) {
                if (i== inscription_ID(n1)) {
                    n = inscription_ID(n2);
                    TabPL.opp[round0] = n2 ;
                    TabPL.opp_points[round0] = score[n] ;
                    TabPL.score[round0] = v ;
                }
                if (i== inscription_ID(n2)) {
                    n = inscription_ID(n1);
                    TabPL.opp[round0] = n1 ;
                    TabPL.opp_points[round0] = score[n] ;
                    TabPL.score[round0] = OPPONENT_SCORE(v) ;
                }
            }
            eloID = registered_players->list[i]->ID;
            if (player_was_present(eloID, round0) && polar2(eloID, round0)==0) { /* joue Bip */
                TabPL.score[round0] = OPPONENT_SCORE(bye_score) ;
            }
        }
        TabPL.player_points = score[i] ;
        PlayerTiebreak(&TabPL, current_round - 1, &tieBreak[i], &discs, &SO) ;
    }
}
