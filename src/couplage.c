/*
 * couplage.c: cree une instance de la classe "couplage", qui est simplement un ensemble de couples
 *  {(x1,x2),(x3,x4)..(x2n-1,x2n)} disjoints, avec des valeurs. Les operations fournies sont:
 *  accoupler deux entiers, decoupler un entier, obtenir la polarite d'un entier,
 *  et un iterateur pour obtenir la liste de toutes les paires.
 *
 * (EL) 02/07/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, retour a la version anterieure de la fonction 'joueur_etait_present()'
 *                   qui ne teste plus le cas de la ronde courante. Evite le bug de la
 *                   correction d'un resultat d'une ronde precedente.
 * (EL) 16/07/2012 : v1.34, modification de la fonction 'joueur_etait_present()' pour inclure
 *                   aussi le cas de la ronde courante.
 *                   Ajout dans la fonction 'sauver_fichier_appariements()' du test
 *                   pour decider de la sauvegarde au format xml.
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, Modification de la structure de sauvegarde du fichier 'Nouveaux'.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : changement des 'fopen()' en 'myfopen_in_subfolder()'
 * (EL) 12/02/2007 : Modification de 'affiche_appariements()' pour qu'il affiche
 *                   le nom du tournoi au debut.
 * (EL) 02/02/2007 : Changement du type de 'tieBreak[]' en double
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 *
 * couplage.c: create an instance of class "couplage" which is simply a set of disjoint couples
 *  {(x1,x2),(x3,x4)..(x2n-1,x2n)}, with values. Operations are: couple two integers, separate a integer,
 *  get an integer polarity and an iterator to obtain list of all pairs.
 *
 * (EL) 02/07/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, rewind to previous version of function 'joueur_etait_present()'
 *                   which doesn't test current round anymore. Prevents the bug of
 *                   correcting a result from a previous round.
 * (EL) 16/07/2012 : v1.34, Modification of function 'player_was_present()' to include current round
 *                   Add a test in function 'save_pairings_file()'
 *                   to decide if xml save is needed.
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, change 'nouveaux" file structure
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : change all 'fopen()' to 'myfopen_in_subfolder()'
 * (EL) 12/02/2007 : Modify 'display_pairings()' so that it displays
 *                   tournament fullname at the start.
 * (EL) 02/02/2007 : tieBreak[] is changed to 'double' type
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */

#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <ctype.h>
#include <string.h>
#include "discs.h"
#include "player.h"
#include "couplage.h"
#include "global.h"
#include "more.h"


typedef struct _Couple {
    long     first;
    long     second;
    discs_t  value;
    long     table;
    struct  _Couple *next;
} Couple;

#ifdef ENGLISH
# define RND_SAVE           "Saving round %ld"
# define RND_RESULTS        "Results of round %ld"
# define RND_INSC           "Players inscribed for round %ld"
# define NEW_BEGIN_BLOCK    " New players, round %ld"
# define NEW_END_BLOCK      " End of new players for round %ld"
# define MIGR_BEGIN_BLOCK   " Players with changed nationalities, round %ld"
# define MIGR_END_BLOCK     " End of emigrants, round %ld"
# define RND_SINSC          "Saving inscribed players of round %ld"
# define CANT_OPEN          "Can't open file"
# define RND_PAIR           "Pairings of round %ld"
# define INCOMPLETE_P       " (not complete)"
# define RND_SPAIR          "Saving pairings of round %ld"
# define NOT_INSC           "Player %ld is not inscribed"
# define SUPP_REASON        "\nPlayer %s (%ld) hasn't played any game..."
# define SUPP_ASK           "\nShall we suppress completely that player (Y/N)?"
# define MAN_PAIRED         "Paired players"
# define MAN_UNPAIRED       "Lonely players"
# define MAN_PROMPT         "[A]ssociate, [D]issociate, [Z]ap or [Q]uit?"
# define MAN_ASS            "Associate which players?"
# define MAN_DISS           "Dissociate which player?"
# define MAN_ZAP            "Really clear all pairings (Y/N)?"
# define MAN_WHICHP         "Which player?"
# define MAN_ASS_BLACK      "  First player (%s):"
# define MAN_ASS_WHITE      "  Second player (%s):"
# define AFF_NOBODY         "No players inscribed."
# define WHICH_FEATS        "Feats for which player?"
# define HIT_ANY_KEY        "Hit any key..."
# define FIC_TEMP           "Temporary file name: %s"
#else
# define RND_SAVE           "Sauvegarde de la ronde %ld"
# define RND_RESULTS        "Resultats de la ronde %ld"
# define RND_INSC           "Inscrits pour la ronde %ld"
# define NEW_BEGIN_BLOCK    " Nouveaux joueurs, ronde %ld"
# define NEW_END_BLOCK      " Fin des nouveaux joueurs pour la ronde %ld"
# define MIGR_BEGIN_BLOCK   " Joueurs ayant change de nationalite, ronde %ld"
# define MIGR_END_BLOCK     " Fin des joueurs emigres, ronde %ld"
# define RND_SINSC          "Sauvegarde des inscrits de la ronde %ld"
# define CANT_OPEN          "Impossible d'ouvrir le fichier"
# define RND_PAIR           "Appariements de la ronde %ld"
# define INCOMPLETE_P       " (incomplets)"
# define RND_SPAIR          "Sauvegarde des appariements de la ronde %ld"
# define NOT_INSC           "Le joueur %ld n'est pas inscrit"
# define SUPP_REASON        "\nLe joueur %s (%ld) n'a joue aucune partie..."
# define SUPP_ASK           "\nVoulez-vous supprimer completement ce joueur (O/N) ?"
# define MAN_PAIRED         "Joueurs apparies"
# define MAN_UNPAIRED       "Joueurs non apparies"
# define MAN_PROMPT         "[A]pparier, [D]ecoupler, RA[Z] ou [Q]uitter ?"
# define MAN_ASS            "Apparier quels joueurs ?"
# define MAN_ASS_BLACK      "  Premier joueur (%s) :"
# define MAN_ASS_WHITE      "  Deuxieme joueur (%s) :"
# define MAN_DISS           "Decoupler quel joueur ?"
# define MAN_ZAP            "Effacer tous les appariements (O/N) ?"
# define MAN_WHICHP         "Quel joueur ?"
# define AFF_NOBODY         "Aucun joueur n'est encore inscrit."
# define WHICH_FEATS        "Fiche de quel joueur ?"
# define HIT_ANY_KEY        "Pressez une touche..."
# define FIC_TEMP           "Nom du fichier temporaire : %s"
#endif


/*
 * Gestion des rondes - rounds management
 */

static Couple *history[NMAX_ROUNDS];
static long modified_coupling = 0,
previous_nb_registered = 0,
modified_presents = 0;

/*
 * Historique des presences de chaque joueur
 ****
 * Players's presence history
 */

/* 32 bits : taille des entiers "unsigned long" sur le compilateur */
/* 32 bits: 'unsigned long' size on the compiler */
#define INTEGER_SIZE 32
#define NB_SLOTS_BITS  ( 1 + ((NMAX_ROUNDS - 1) / INTEGER_SIZE))

static unsigned long presences_history[MAX_REGISTERED][NB_SLOTS_BITS];


/*
 * Initialisations avant la premiere ronde
 ****
 * First round initialisations
 */

void first_round(void) {
    static long history_inititalized = 0;
    long i,j;


    /*
     * Initialisation de l'historique des couplages
     ****
     * Coupling history initialization
     */
    if (history_inititalized)
        for (current_round = 0; current_round < NMAX_ROUNDS; current_round++)
            zero_coupling();
    for (i=0; i<NMAX_ROUNDS; i++)
        history[i] = NULL;
    history_inititalized = 1;

    /*
     * Initialisation de l'historique des presences
     ****
     * Presences history initialization
     */
    for (i=0; i<MAX_REGISTERED; i++)
        for (j=0; j<NB_SLOTS_BITS; j++)
            presences_history[i][j] = 0;

    /*
     * remettons a zero les scores, pions, departages, etc
     ****
     * scores, discs, tiebreak are zeroed.
     */
    clearScores();

    current_round = 0;
    modified_coupling = 0;
}

/*
 * Passage a la ronde suivante
 ****
 * Move to next round
 */

void next_round(void) {
    long i;

    assert(0 <= current_round && current_round < NMAX_ROUNDS-1);

    /*
     * on enregistre les presents de la ronde dans l'historique
     ****
     * present players at the round are registered in history
     */

    for (i = 0; i < registered_players->n; i++)
        set_history_presence(registered_players->list[i]->ID, current_round, present[i]);

    ++current_round;
    modified_coupling = 1;
}

/*
 * Sauve les resultats de la ronde courante ; les signaux sont supposes etre masques.
 * Aucun controle d'erreur pour l'instant!
 * S'il s'agit de la premiere ronde et que le nombre de joueurs permet un toutes-rondes,
 * alors on sauvegarde la 'table' de numerotation des joueurs.
 ****
 * Save current round results; signals should be blocked. No error control for now.
 * If it is the first round and if number of players enables a round-robin,
 * the players numbering 'table' is also saved.
 */

void _save_round(void) {
    long n1, n2;
    discs_t v;
    FILE *fp;

    fp = myfopen_in_subfolder(workfile_filename, "a", "", 0, 1) ;
#ifdef DEBUG
    fprintf(stderr, RND_SAVE "\n", current_round+1);
#endif
    /* Nous en profitons pour sauvegarder la table du toutes-rondes */
    /* Take the chance to save round-robin table */
    if (current_round == 0) {
        initRoundRobin();
        save_rrTable(fp);
    }
    /* Puis les resultats proprement dits */
    /* Then save results */
    fprintf(fp,"%% " RND_RESULTS "\n\n" "raz-couplage;\n", current_round+1);
    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v)) {
        assert(SCORE_IS_LEGAL(v));
        fprintf(fp,"(%06ld %-5s %06ld %s);\n", n1, discs2string(v),
                n2, discs2string(OPPONENT_SCORE(v)));
    }
    fprintf(fp,"ronde-suivante;\n\n");
    fclose(fp);
    backup_workfile() ;
    modified_coupling = 0;
}

/*
 * Si de nouveaux joueurs se sont inscrits, ou si des joueurs ont quitte
 * ou reintegre le tournoi, ou s'ils ont change de nationalite(!),
 * alors cette fonction effectue les sauvegardes necessaires dans le
 * fichier intermediaire et/ou le fichier des nouveaux. Les signaux sont
 * supposes bloques.
 ****
 * If new players registered or if players have left or came back,
 * or if a player changed nationality, then this function does the
 * necessary saves in the work file and/or the new players file.
 * Signals should be blocked.
 */

void _save_registered(void) {
    long i;
    FILE *fp;
    Player *j;

    if (previous_nb_registered == registered_players->n /* pas de nouveaux inscrits  - no new player */
        && modified_presents == 0           /* personne n'est entre ou sorti - no one entered or left */
        && emigrant_players->n == 0)        /* ou n'a change de pays - no one changed country */
        return;

    /* ecriture dans le fichier intermediaire 'papp-internal-workfile.txt' */
    /* write in the Papp workfile */
    fp = myfopen_in_subfolder(workfile_filename, "a", "", 0, 1) ;
#ifdef DEBUG
    fprintf(stderr, RND_SINSC "\n", current_round+1);
#endif
    fprintf(fp,"%% " RND_INSC "\n\n&", current_round+1);
    for (i=0; i<registered_players->n; i++)
        if (registered_players->list[i]->ID > 0)
            fprintf(fp,"%s%c%06ld", i!=0 && i%10==0 ? "\n  " : " ",
                    present[i]?'+':'-',
                    registered_players->list[i]->ID);
    fprintf(fp,";\n\n");
    fclose(fp);
    backup_workfile() ;

    /* Mais il faut egalement sauvegarder les nouveaux joueurs */
    /* But new players must also be saved */
    fp = myfopen_in_subfolder(new_players_filename, "a", "", 0, 1) ;
    for (i=0; i<new_players->n; i++) {
        j = new_players->list[i];
        if (j->ID > 0)
            fprintf(fp,"%6ld %s, %s\t{%s}\n", j->ID, j->family_name, j->firstname, j->country);
    }
    fclose(fp);

    /* On met les nouveaux en commentaires dans le fichier intermediaire */
    /* New players are added as comment in the workfile */
    fp = myfopen_in_subfolder(workfile_filename, "a", "", 0, 1) ;
    if (new_players->n > 0) {
        fprintf(fp,"%%%%  ");
        fprintf(fp, NEW_BEGIN_BLOCK , current_round+1);
        fprintf(fp,"\n%%\n");
        for (i=0; i<new_players->n; i++) {
            j = new_players->list[i];
            if (j->ID > 0)
                fprintf(fp,"%%_%% %6ld %s, %s\t{%s}\n", j->ID, j->family_name, j->firstname, j->country);
        }
        fprintf(fp,"%%\n%%");
        fprintf(fp, NEW_END_BLOCK, (current_round+1));
        fprintf(fp,"\n\n\n");
    }
    fclose(fp);
    backup_workfile() ;

    /* Les nouveaux sont connus maintenant */
    /* New players are now known */
    emptyList(new_players);

    /* Ainsi que les joueurs emigres */
    /* And also emigrate players */
    fp = myfopen_in_subfolder(new_players_filename, "a", "", 0, 1) ;
    for (i=0; i<emigrant_players->n; i++) {
        j = emigrant_players->list[i];
        if (j->ID > 0)
            fprintf(fp,"%%_ %6ld {%s}\n", j->ID, j->country);
    }
    fclose(fp);

    /* On met aussi les emmigres en commentaires dans le fichier intermediaire */
    /* emigrate players are also added as comment in the workfile */
    fp = myfopen_in_subfolder(workfile_filename, "a", "", 0, 1) ;
    if (emigrant_players->n > 0) {
        fprintf(fp,"%%%%  ");
        fprintf(fp, MIGR_BEGIN_BLOCK, (current_round+1));
        fprintf(fp,"\n%%\n");
        for (i=0; i<emigrant_players->n; i++) {
            j = emigrant_players->list[i];
            if (j->ID > 0)
                fprintf(fp,"%% %6ld %s {%s}\n", j->ID, j->fullname, j->country);
        }
        fprintf(fp,"%%\n%%");
        fprintf(fp, MIGR_END_BLOCK, (current_round+1));
        fprintf(fp,"\n\n\n");
    }
    fclose(fp);
    backup_workfile() ;
    /* Les emmigres sont connus maintenant */
    /* emigrate players are now known */
    emptyList(emigrant_players);

    previous_nb_registered = registered_players->n;
    modified_presents = 0;
}

/*
 * Sauver les appariements s'ils ont change depuis la derniere fois, i.e.
 * si modified_coupling != 0. Cette fonction n'est utilisee qu'en mode
 * de sauvegarde immediate. Les signaux sont supposes bloques.
 ****
 * Save the pairings if they have changed since last time, i.e.
 * if modified_coupling != 0. This function is only used in the
 * immediate save mode. Signals should be blocked.
 */

void _save_pairings(void) {
    long n1, n2;
    discs_t v;
    FILE *fp;

    if (modified_coupling == 0)
        return;
    fp = myfopen_in_subfolder(workfile_filename, "a", "", 0, 1) ;
#ifdef DEBUG
    fprintf(stderr, RND_SPAIR "\n", current_round+1);
#endif
    assert(fp);
    fprintf(fp,"%% " RND_PAIR "\n\n" "raz-couplage;\n", current_round+1);
    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v))
        fprintf(fp, COUPON_IS_EMPTY(v) ? "(%06ld %06ld);\n" : "(%06ld %06ld %s);\n",
                n1, n2, discs2string(v));
    fprintf(fp,"\n");
    fclose(fp);
    backup_workfile() ;
    modified_coupling = 0;
}


/*
 * Recreer totalement un fichier 'papp-internal-workfile.txt' a partir de l'historique.
 * Utilisee par exemple apres la correction d'un resultat, ou
 * lorque l'on desinscrit un joueur qui n'a joue aucune partie.
 ****
 * Completely recreate the papp workfile from history. Used for example when
 * a result is corrected or when a player who hasn't played any game withdraws.
 */

void _recreate_workfile(void) {
    long cur_round;
    long cur_registered;
    long cur_modified_coupling;
    long cur_modified_presents;
    long cur_presents[MAX_REGISTERED];
    char *cur_workfile_name = NULL;
    char temp_file[50] = "__papp.tmp";
    char save_file[50] = "__papp.bak";
    long error;
    FILE *papp_file ;

    long i, player_is_present;
    long present_before[MAX_REGISTERED];

    /* Sauvegarder les variables globales de Papp */
    /* Save Papp's global variables */
    cur_round              = current_round;
    cur_registered         = previous_nb_registered;
    cur_modified_coupling  = modified_coupling;
    cur_modified_presents  = modified_presents;
    for (i = 0; i < registered_players->n; i++)
        cur_presents[i]    = present[i];
    COPY(workfile_filename, &cur_workfile_name);


    /* On ecrase le fichier "__papp.tmp" (s'il y en a un) */
    /* "__papp.tmp" file is rewritten (if it exists)      */
    error = remove(temp_file);
    /* On fait les sauvegardes intermediaires dans "__papp.tmp" */
    /* intermediate saves are done in file "__papp.tmp"         */
    COPY(temp_file, &workfile_filename);

    /* d'abord les infos du tournoi */
    /* First tournament info        */
    init_workfile(NONEXISTING) ;
    /* Puis le fichier des nouveaux */
    /* Then new players file        */
    if ((papp_file = myfopen_in_subfolder(workfile_filename, "ab", "", 0, 0)) != NULL) {
        copy_nouveaux_file(papp_file) ;
        fclose(papp_file) ;
    }

#ifdef DEBUG
    fprintf(stderr, FIC_TEMP "\n", workfile_filename);
#endif

    /* on initialise le tableau des presents */
    /* present players array is initialized  */
    for (i = 0; i < registered_players->n; i++)
        present[i] = 0;

    /* reconstitution du tournoi d'apres l'historique */
    /* rebuild tournament from history                */
    for (current_round = 0; current_round <= cur_round - 1 ; current_round++) {

        for (i = 0; i < registered_players->n; i++)  {
            present_before[i] = present[i];
            present[i] = player_was_present(registered_players->list[i]->ID, current_round);
        }

        modified_presents = 0;
        for (i = 0; i < registered_players->n; i++)
            modified_presents |= (present[i] != present_before[i]);
        /* toujours sauver les inscrits de la ronde 1 */
        /* Always save round 1 registered players     */
        if (current_round == 0) modified_presents = 1;

        /* on sauve les presents/absents de cette ronde passee... */
        /* Saving presents/absents from this past round           */
        if (modified_presents)
            _save_registered();
        /* ... et les resultats --- ... and results */
        _save_round();
    }

    current_round = cur_round;

    /* presents de la ronde en cours */
    /* Current round presents        */
    modified_presents = 0;
    for (i = 0; i < registered_players->n; i++)  {
        player_is_present = cur_presents[i];
        if (player_is_present != present[i]) {
            present[i] = player_is_present;
            modified_presents = 1;
        }
    }
    /* toujours sauver les inscrits de la ronde 1 */
    /* Always save round 1 registered players     */
    if (current_round == 0) modified_presents = 1;

    /* on sauve les presents/absents de la ronde en cours... */
    /* Saving presents/absents from this current round       */
    if (modified_presents)
        _save_registered();
    /* ... et les appariements eventuels --- and existing pairings */
    assert((current_round >= 0) && (current_round < NMAX_ROUNDS));
    if (history[current_round] != NULL)  {
        modified_coupling = 1;
        _save_pairings();
    }


    /* on renomme l'ancien fichier "papp-internal-workfile.txt" en "__papp.bak" */
    /* Former workfile is renamed to "__papp.bak" */
    error = remove(save_file);
    error = rename(cur_workfile_name,save_file);
    /* puis le fichier "__papp.tmp" en "papp-internal-workfile.txt" */
    /* And temporary file "__papp.tmp" to workfile                  */
    error = rename(workfile_filename,cur_workfile_name);

    /* Termine, retablir les variables globales de papp */
    /* Finished, retrieve Papp's global variables       */
    current_round              = cur_round;
    previous_nb_registered = cur_registered;
    modified_coupling   = cur_modified_coupling;
    modified_presents  = cur_modified_presents;
    for (i = 0; i < registered_players->n; i++)
        present[i]     = cur_presents[i];
    COPY(cur_workfile_name, &workfile_filename);
    backup_workfile() ;
}


/*
 * Les deux fonctions suivantes signalent a PAPP que la liste des inscrits/presents
 * (resp. des appariements) est a jour et ne necessite pas de sauvegarde -- voir main.c.
 ****
 * The following two functions tell PAPP that the list of registered/present players
 * (or pairings) is up-to-date and do not need saving -- see main.c
 */

void do_not_save_registered(void) {
    previous_nb_registered = registered_players->n;
    modified_presents = 0;
}

void do_not_save_pairings(void) {
    modified_coupling = 0;
}

/*
 * Operations sur les couplages
 ****
 * Coupling operations
 */

void zero_coupling(void) {
    Couple *p, *q;

    for (p = history[current_round]; p; p = q) {
        q = p->next;
        free(p);
        modified_coupling = 1;
    }
    history[current_round] = NULL;
}

long uncouple(long n) {
    Couple **p, *q;

    for (p = &history[current_round]; (q = *p) != NULL; p = &(q->next))
        if (q->first == n || q->second == n) {
            /* enlever cette paire --- remove this pair */
            *p = q->next;
            free(q);
            modified_coupling = 1;
            return 1;
        }
    return 0;
}

void make_couple(long n1, long n2, discs_t value) {
    Couple *q, *p;

    uncouple(n1);
    uncouple(n2);
    if (n1 == n2)
        return;
    CALLOC(q, 1, Couple);
    q->first = n1;
    q->second  = n2;
    q->value  = value;
    q->table   = 0;
    q->next    = NULL;

    /* ajouter q a la fin de la liste --- add q to end of list */
    if (history[current_round] == NULL)
        history[current_round] = q;
    else {
        p = history[current_round];
        while (p->next)
            p = p->next;
        p->next = q;
    }

    modified_coupling = 1;
}

/*
 * polarity(n): renvoie 0 si le joueur n'est pas apparie, 1 s'il a les noirs
 * et 2 s'il a les blancs. La fonction polar2() prend un argument
 * supplementaire qui est le ID de la ronde.
 ****
 * polarity(n): returns 0 if the player isn't paired, 1 if she/he's Black,
 * and 2 if she/he's White. polar2() function takes another argument
 * which is round ID.

 */

long polarity(long n) {
    return polar2 (n, current_round);
}

long polar2 (long n, long round) {
    Couple *q;

    if (round < 0 || round >= NMAX_ROUNDS)
        return 0;
    for (q = history[round]; q; q = q->next) {
        if (q->first == n)
            return 1;
        if (q->second == n)
            return 2;
    }
    return 0;
}

/*
 * renvoie un pointeur sur la variable contenant le "score" de n1 contre
 * n2, ou NULL si n1 n'a pas joue contre n2 avec les noirs. Comme ce
 * pointeur peut etre utilise pour modifier cette variable, nous levons
 * le drapeau modified_coupling. Usage typique:
 *
 *    long n1, n2, v;
 *    *couple_value(n1,n2) = v;
 ****
 * returns a pointer to the variable containing the "score" of n1 against n2,
 * or NULL if n1 didn't play Black against n2. As this pointer can be used to
 * modify the variable, the modified_coupling flag is raised. Typical usage:
 *
 *    long n1, n2, v;
 *    *couple_value(n1,n2) = v;
 */

discs_t *couple_value(long n1, long n2) {
    Couple *q = history[current_round];

    while (q)
        if (q->first == n1 && q->second == n2) {
            /*
             * Quelqu'un peut se servir de cette adresse pour
             * modifier les resultats des appariements.
             ****
             * Someone can use this address to change results
             */
            modified_coupling = 1;
            return &(q->value);
        }
            else q = q->next;
    return NULL;
}

/*
 * Renvoie le nombre d'appariements de la ronde <round_nbr>
 ****
 * Returns number of pairings in round <round_nbr>
 */

long couples_nbr(long round_nbr) {
    long counter;
    Couple *q;

    if (round_nbr < 0 || round_nbr >= NMAX_ROUNDS || round_nbr > current_round)
        return 0;

    counter = 0;
    for (q = history[round_nbr]; q; q = q->next)
        counter++;
    return counter;
}

/*
 * Pour iterer sur l'ensemble des couples, on ecrira :
 * (On pourra tester v == UNKNOWN_SCORE pour indiquer que le score n'est pas encore connu.)
 ****
 * To iterate on the set of couples, we can write:
 * (v == UNKNOWN_SCORE can be tested to show that score isn't yet known.)
 *
 *    long n1, n2;
 *    discs_t v;
 *    round_iterate(round_number);
 *    while (next_couple(&n1,&n2,&v)) {
 *        ... Utiliser/Use n1,n2,v ...
 *    }
 */

static Couple *pointer;

void round_iterate(long round_nbr) {
    pointer = (round_nbr >= 0 && round_nbr < NMAX_ROUNDS) ?
    history[round_nbr] : NULL;
}

long next_couple(long *n1, long *n2, discs_t *value) {
    if (pointer == NULL)
        return 0;
    *n1 = pointer->first;
    *n2 = pointer->second;
    *value = pointer->value;
    pointer = pointer->next;
    return 1;
}

/*
 * Fonctions diverses pour savoir si un joueur est present, ainsi que son score
 ****
 * Functions to know if a player is present, and his score.
 */

long         present[MAX_REGISTERED];
long         score[MAX_REGISTERED];
discs_t      nbr_discs[MAX_REGISTERED];
double       tieBreak[MAX_REGISTERED];
long         last_float[MAX_REGISTERED];
long         team_score[MAX_REGISTERED];

/*
 * Fait entrer (direction == 1) ou sortir (direction == 0) le joueur de ID Elo ID_player.
 * Si c'est impossible, les erreurs sont affichees a l'ecran.
 ****
 * Get in (direction == 1) or out (direction == 0) player whose ELO ID is ID_player.
 * If it's impossible, errors are displayed on screen.
 */

void player_InOut(long ID_player, long direction) {
    /* direction: 0=sortir/get out, 1=entrer/ get in */
    long i,round0,n1,n2,never_played, ret;
    discs_t v;
    char *name;
    char string[500];

    for (i=0; i<registered_players->n; i++)
        if (registered_players->list[i]->ID == ID_player) {
            name = registered_players->list[i]->fullname;
            /* Tester l'etat actuel du joueur - test player actual status */

            if (present[i] == direction)
#ifdef ENGLISH
                printf("Player %s (%ld) was already %s\n", name, ID_player,
                       direction ? "in" : "out");
            else
                printf("Player %s (%ld) %s the tournament\n", name, ID_player,
                       direction ? "enters" : "leaves");
#else
                printf("Le joueur %s (%ld) etait deja %s\n", name, ID_player,
                       direction ? "present" : "absent");
            else
                printf("Le joueur %s (%ld) %s\n", name, ID_player,
                       direction ? "entre" : "sort");
#endif

            present[i] = direction;
            absents_uncoupling();
            modified_presents = 1;

            /* En cas de sortie, et si le joueur n'a joue aucune partie,
             * on propose de le supprimer completement du tournoi.
             ****
             * If going out, and if the player hasn't played any game,
             * papp proposes to take him away from the tournament.
             */
            never_played = 1;
            if (direction == 0) {
                for (round0 = 0; round0 < current_round; round0++) {
                    round_iterate(round0);
                    while (next_couple(&n1, &n2, &v))
                        if ((n1 == ID_player) || (n2 == ID_player)) never_played = 0;
                }
                if (never_played) {
                    printf(SUPP_REASON, name, ID_player);
                    sprintf(string, SUPP_ASK);
                    if (yes_no(string)) {

                        /*
                         * on met temporairement le ID du joueur a zero,
                         * de maniere a ce qu'il ne soit pas mis dans le fichier
                         * intermediaire "papp-internal-workfile.txt" que l'on regenere.
                         ****
                         * Player's ID is temporarely set to 0 so that she/he isn't put
                         * in the intermediate "papp-internal-workfile.txt" file we are
                         * regenerating.
                         */
                        registered_players->list[i]->ID = 0;
                        recreate_workfile();
                        registered_players->list[i]->ID = ID_player;

                        /* on relit immediatement le nouveau fichier "papp-internal-workfile.txt"
                         * The new "papp-internal-workfile.txt" file is immediately reread */
                        emptyList(registered_players);
                        first_round();
                        ret = read_file(workfile_filename, CONFIG_F);
                        if (ret > 0)
                            fatal_error("ERREUR DE FICHIER dans la fonction player_InOut");
                    }
                    printf("\n"HIT_ANY_KEY);
                }
            }
            return;
        }
/* Pas trouve ? - Not found? */
    printf(NOT_INSC " !?\n", ID_player);
    beep();
}

/*
 * Faire sortir le joueur du tournoi avec present[i]=0 ne suffit pas a detruire son appariement;
 * il faut invoquer la fonction suivante, qui decouple tous les joueurs absents.
 ****
 * Getting a player out with present[i]=0 isn't enough to destroy his pairing.
 * The following function must be called; it uncouples all absent players.
 */

void absents_uncoupling(void) {
    long i;

    for (i = 0; i < registered_players->n; i++)
        if (!present[i])
            uncouple(registered_players->list[i]->ID);
}

/*
 * La fonction suivante permet d'enregistrer dans l'historique la presence
 * (presence =1) ou l'absence (presence=0) d'un joueur a une ronde donnee.
 ****
 * The following function records in history the presence (presence=1) or
 * absence (presence=0) of a player on a given round.
 */

void set_history_presence(long ID, long which_round, long presence) {
    long i, which_bit_field;
    unsigned long mask;


    assert(which_round >= 0 && which_round <= current_round);
    assert(ID >= 0);

    which_bit_field = (which_round / INTEGER_SIZE);
    mask = ((unsigned long)1) << (which_round % INTEGER_SIZE);

    assert(which_bit_field >= 0  &&  which_bit_field < NB_SLOTS_BITS);

    for (i=0; i<registered_players->n; i++)
        if (registered_players->list[i]->ID == ID)  {
            if (presence != ((presences_history[i][which_bit_field] & mask) != 0))
                presences_history[i][which_bit_field] ^= mask;
        }
}

/*
 * La fonction suivante permet de tester si un joueur etait present a une
 * ronde passee. Note : pour la ronde courante, utiliser plutot le tableau
 * global "present[]" (car la variable 'round' peut avoir ete modifiee).
 ****
 * Following function enables to test whether a player was present in a previous round.
 * Note: for current round, rather use global array "present[]"
 * as variable "round" may have been modified.
 */

long player_was_present(long ID, long which_round) {
    long i, which_bit_field;
    unsigned long mask;


    assert(which_round >= 0);
    assert(ID >= 0);

    which_bit_field = (which_round / INTEGER_SIZE);
    mask = ((unsigned long)1) << (which_round % INTEGER_SIZE);

    assert(which_bit_field >= 0 && which_bit_field < NB_SLOTS_BITS);

    for (i=0; i<registered_players->n; i++)
        if (registered_players->list[i]->ID == ID) {
	        return ((presences_history[i][which_bit_field] & mask) != 0);
        }

    /* le joueur n'est meme pas inscrit ! - Player isn't even registered! */
    return 0;
}


/*
 * Maintenant la vraie routine de manipulation des appariements.
 ****
 * Now the true pairings manipulation function.
 */

void pairings_manipulate(void) {
    char string[256], c , *line;
    long l, lmax, nbc, i, k, n1, n2, _n1, _n2, passe;
    discs_t v;

    for(;;) {
        /* Afficher la liste des joueurs apparies - display list of paired players */
        clearScreen();
        printf(MAN_PAIRED " :\n");
        lmax = nbc = 0;
        for (passe=0; passe<2; passe++) {
            k = 0;
            round_iterate(current_round);
            while (next_couple(&n1, &n2, &v)) {

#ifdef DISPLAY_ALPHA_COUPLING
                sprintf(string,"%-8.8s(%6ld)--%-8.8s(%6ld) ",
                        findPlayer(n1)->fullname,n1, findPlayer(n2)->fullname,n2);
#else
                sprintf(string,"%ld--%ld",n1,n2);
#endif

                if (passe) {
                    /* Deuxieme passe, faire l'affichage  -
                     * second pass, do display */
                    assert(nbc > 0);
                    printf("%c%-*s", k++%nbc ? ' ':'\n', (int)lmax, string);
                } else {
                    /* Premiere passe, calculer la largeur maximum -
                     * first pass, calculate maximum width */
                    l = strlen(string);
                    if (l > lmax)
                        lmax = l;
                }
            }
            if (lmax == 0)
                break;
            nbc = nbrOfColumns / (lmax + 1);
        }
        /* Afficher la liste des joueurs non apparies - Display non-paired players */
        printf("\n\n" MAN_UNPAIRED " :\n");
        lmax = nbc = 0;
        for (passe=0; passe<2; passe++) {
            k = 0;
            for (i=0; i<registered_players->n; i++)
                if (present[i] && !polarity(registered_players->list[i]->ID)) {
#ifdef DISPLAY_ALPHA_COUPLING
                    sprintf(string,"%-8.8s(%ld)",
                            registered_players->list[i]->fullname,registered_players->list[i]->ID);
#else
                    sprintf(string,"%ld",registered_players->list[i]->ID);
#endif

                    if (passe) {
                        /* Deuxieme passe - Second pass */
                        assert(nbc > 0);
                        printf("%c%-*s", k++%nbc ? ' ':'\n', (int)lmax, string);
                    } else {
                        /* Premiere passe - First pass */
                        l = strlen(string);
                        if (l > lmax)
                            lmax = l;
                    }
                }
            if (lmax == 0)
                break;
            nbc = nbrOfColumns / (lmax + 1);
        }
        printf("\n\n");
        /* Et afficher le prompt - Print prompt */
        printf(MAN_PROMPT);
        c = read_key(); c = tolower(c);
        switch(c) {
            case 'v':
                display_pairings(NULL, 0);
                break;
            case 'l':
                display_registered(NULL);
                break;
            case 'f':
                clear_line();
                printf("\n");
                if ((n1 = selectPlayerFromKeyboard(WHICH_FEATS, registered_players, &line)) >= 0)
                    /*
                     * Il n'est pas necessaire de verifier que le joueur est
                     * inscrit puisque fiche_individuelle() le verifie elle-meme.
                     ****
                     * No need to check whether player is registered as
                     * individualSheet() will do it itself.
                     */
                    individualSheet(n1, NULL);
                break;
            case 'z':
                clear_line();
                printf("\n");
                if (yes_no(MAN_ZAP))
                    zero_coupling();
                break;
            case 'a':
                clear_line();
                printf("\n");
                printf(MAN_ASS);
                printf("\n");

                sprintf(string, MAN_ASS_BLACK, color_1);
                if (((n1 = selectPlayerFromKeyboard(string, registered_players, &line)) >= 0)
                    && ((_n1= inscription_ID(n1)) >=0) && present[_n1])
                    puts(coupon(findPlayer(n1), NULL, UNKNOWN_SCORE));
                else { beep(); break;}

                    sprintf(string, MAN_ASS_WHITE, color_2);
                if (((n2 = selectPlayerFromKeyboard(string, registered_players, &line)) >= 0)
                    && ((_n2= inscription_ID(n2)) >=0) && present[_n2])
                    puts(coupon(findPlayer(n2), NULL, UNKNOWN_SCORE));
                else { beep(); break;}

                make_couple(n1, n2, UNKNOWN_SCORE);
                break;
            case 'd':
                clear_line();
                printf("\n");
                if (((n1 = selectPlayerFromKeyboard(MAN_DISS, registered_players, &line)) >= 0) &&
                    ((_n1= inscription_ID(n1)) >=0 ) &&
                    present[_n1] )
                    uncouple(n1);
                else beep();
                break;
            case 'q':
            case 3:        /* ^C */
                return;
            default:
                beep();
                break;
        } /* switch */
    } /* for(;;) */
}

/*
 * Cherche le couplage de la ronde <round_nbr> impliquant les joueurs <nbr_player1> et <nbr_player2>,
 * les reordonne eventuellement (changement de couleur) et met le score <value> au premier joueur
 * (changement de score)
 * Renvoie :
 *   1  si le changement a ete effectue
 *   0  si le changement n'a pas ete effectue parce que les joueurs et le bon score etait deja dans l'historique
 *  -1  si le changement n'a pas ete effectue parce que les joueurs n'ont pas joue ensemble a la ronde <round_nbr>
 ****
 * Look for the pairing from round <round_nbr> involving players <nbr_player1> and <nbr_player2>,
 * eventually resort them (change of colour) and set first player score to <value> (change of score)
 * Returns:
 *   1  if the change has been made
 *   0  if no change was made because players and score were already in history
 *  -1  if no change was made because players didn't play together at round <round_nbr>
 */

long change_result(long round_nbr, long nbr_player1, long nbr_player2, discs_t value) {
    Couple *q;

    assert(round_nbr >= 0 && round_nbr <= current_round && round_nbr < NMAX_ROUNDS);
    assert(nbr_player1 > 0);
    assert(nbr_player2 > 0);
    assert(SCORE_IS_LEGAL(value));


    for (q = history[round_nbr]; q; q = q->next) {

        if ((q->first == nbr_player1) &&
            (q->second  == nbr_player2) &&
            SCORES_EQUALITY(q->value, value))
            return 0;  /* pas besoin de changer ! - no need to change! */

        if (((q->first == nbr_player1) && (q->second == nbr_player2)) ||
            ((q->first == nbr_player2) && (q->second == nbr_player1))) {
            /* changer les joueurs et le score - change players and score */
            q->first = nbr_player1;
            q->second  = nbr_player2;
            q->value  = value;
            return 1;
        }
    }

    /* pas trouve - not found */
    return -1;
}


/*
 * Affecte un ID de table au match opposant n1 et n2 a la ronde round_nbr.
 ****
 * Assigns table ID to the game between n1 and n2 at round <round_nbr>
 */

void assign_table_number(long round_nbr, long n1, long n2, long table_ID) {
    Couple *q;

    assert(round_nbr >= 0 && round_nbr <= current_round && round_nbr < NMAX_ROUNDS);
    assert(n1 > 0);
    assert(n2 > 0);

    for (q = history[round_nbr]; q; q = q->next) {
        if (((q->first == n1) && (q->second == n2)) ||
            ((q->first == n2) && (q->second == n1))) {
            /* changer le ID de la table - change table ID */
            q->table = table_ID;
        }
    }
}

/*
 * Renvoie le ID de table du match entre n1 et n2 de la ronde <round_nbr>, ou 0
 * si on ne trouve pas la table (pas de match, ou pas encore de table affectee, etc)
 ****
 * Returns table ID for the game between n1 and n2 at round <round_nbr>, or 0 if
 * table isn't found (no game or no table yet assigned, etc).
 */

long find_table_number(long round_nbr, long n1, long n2) {
    Couple *q;

    assert(round_nbr >= 0 && round_nbr <= current_round && round_nbr < NMAX_ROUNDS);
    assert(n1 > 0);
    assert(n2 > 0);

    for (q = history[round_nbr]; q; q = q->next) {
        if (((q->first == n1) && (q->second == n2)) ||
            ((q->first == n2) && (q->second == n1))) {
            return (q->table);
        }
    }
    return 0;
}

/*
 * Reaffecte tous les numeros de tables de la ronde courante.
 * On utilise le meme tri que pour l'affichage des coupons.
 *****
 * Reassigns all table IDs for current round, using same sort as pairings display.
 */

void tables_numbering(void) {
    long i, n1, n2;
    discs_t v;
    Pair *list;
    long pairs_number;

    assert(current_round >= 0 && current_round < NMAX_ROUNDS);


    CALLOC(list, 1 + registered_players->n / 2, Pair);
    pairs_number = 0;

    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v)) {
        i = pairs_number++;
        list[i].j1 = findPlayer(n1);
        list[i].j2 = findPlayer(n2);
        list[i].score = v;
        list[i].sort_value = tables_sort_criteria(n1, n2);
    }
    if (current_round >= 1)
        SORT(list, pairs_number, sizeof(Pair), pairs_sort);

    for (i = 0; i < pairs_number; i++)
        assign_table_number(current_round, (list[i].j1)->ID, (list[i].j2)->ID, i + 1);

    free(list);
}


/*
 * calcule le nombre de joueurs presents et non apparies
 ****
 * computes number of present but unpaired players
 */

long nbr_unpaired_players(void) {
    long n, i;

    for (i = n = 0; i < registered_players->n; i++)
        if (present[i] && polarity(registered_players->list[i]->ID)==0)
            ++n;
    return n;
}

int pairs_sort(const void *ARG1, const void *ARG2) {
	const Pair *p1 = (const Pair *) ARG1 ;
	const Pair *p2 = (const Pair *) ARG2 ;
    double diff;

    diff = (p2->sort_value) - (p1->sort_value);
    if (diff > 0)
        return 1;
    else if (diff < 0)
        return -1;
    else
        return 0;
}

/*
 * Les paires de joueurs apparies sont triees par ordre decroissant de
 * "valeur", cette valeur etant le score du joueur le mieux classe, avec
 * comme critere secondaire la somme des scores des joueurs.
 * Ceci est susceptible de changer dans les prochaines versions.
 ****
 * Pairs of paired players are sorted in reverse order of "value".
 * This value is the best player's score, then sum of players' scores.
 * This may change in future versions.
 */

double tables_sort_criteria(long n1, long n2) {

    return 100000.0 * max_of(score[inscription_ID(n1)], score[inscription_ID(n2)])
    + 100.0*(score[inscription_ID(n1)] + score[inscription_ID(n2)] )
    + inscription_ID(n1)/10.0;
}

/*
 * Affichage des appariements - Pairings display
 */

void display_pairings(const char *filename, long full_results) {
    char string[256];
    char *tournamentName ;
    long i, n1, n2;
    discs_t v;
    Player *j;
    Pair *list;
    long pairs_nbr;

    /* Peut-etre devons-nous sauvegarder l'etat - maybe state should be saved */
    if (immediate_save) {
        save_registered();
        save_pairings();
    }
    more_init(filename);
    if (registered_players->n == 0) {
        more_line(AFF_NOBODY);
        more_close();
        return;
    }

    /* Pour afficher le fullname du tournoi en tete - tournament full name is displayed first */
    tournamentName = malloc(strlen(tournament_name)+15) ;
    if (tournamentName != NULL) {
        sprintf(tournamentName, "*** %s ***\n", tournament_name) ;
        more_line(tournamentName) ;
        free(tournamentName) ;
    }
    sprintf(string, full_results ? (RND_RESULTS "%s :") : (RND_PAIR "%s :"), current_round+1,
        nbr_unpaired_players() > 1 ? INCOMPLETE_P : "");
    more_line(string);
    more_line("");

    /* D'abord afficher les joueurs apparies - First display paired players */
    CALLOC(list, 1 + registered_players->n / 2, Pair);
    pairs_nbr = 0;

    round_iterate(current_round);
    while (next_couple(&n1, &n2, &v)) {
        i = pairs_nbr++;
        list[i].j1 = findPlayer(n1);
        list[i].j2 = findPlayer(n2);
        list[i].score = v;
        list[i].sort_value = tables_sort_criteria(n1, n2);
    }
    if (current_round >= 1)
        SORT(list, pairs_nbr, sizeof(Pair), pairs_sort);

#ifdef TABLES_NUMBER
    {
        char tampon[400];
        for (i = 0; i < pairs_nbr; i++){

            if (1) /*
                    * Afficher tous les numeros de table. Pour afficher un ID de table sur 5,
                    * utiliser la ligne ci-dessous
                    * if (((i % 5) == 4) || (i == 0))
                    ****
                    * Display all tables ID. To display one ID each 5, use line:
                    * if (((i % 5) == 4) || (i == 0))
                    */
                sprintf(tampon, "%2ld %s", i+1 , coupon(list[i].j1, list[i].j2, list[i].score));
            else
                sprintf(tampon, "   %s" , coupon(list[i].j1, list[i].j2, list[i].score));
            more_line(tampon);
        }
    }
#else
    for (i = 0; i < pairs_nbr; i++)
        more_line(coupon(list[i].j1, list[i].j2, list[i].score));
#endif
    free(list);

    /* Puis les joueurs non apparies - Then non-paired players */
    more_line("");
    for (i = 0; i < registered_players->n; i++)
        if (present[i]) {
            j = (registered_players->list)[i];
            if (polarity(j->ID) == 0)
                more_line(coupon(j, NULL, UNKNOWN_SCORE));
        }
    /* Termine - Finished */
    more_close();
    /* Creons les numeros de tables, si ce n'est deja fait  - Create tables ID if not already done */
    tables_numbering();
}


/*
 * sauvegardes dans les fichiers "ronde###.txt"
 ****
 * Saving in files "round###.txt"
 */

void save_pairings_file(void) {
    char    *filename;
    long     i;

    if (pairings_file_save) {
        filename = numbered_filename(pairings_filename, current_round + 1);
        display_pairings(filename, 0);
        if (automatic_printing)
            for (i = 0; i < print_copies; i++)
                print_file(filename);
    }

	/* Faut-il generer le fichier XML des resultats ? -
	 * Should we generate an XML file with pairings? */
	if (generate_xml_files) {
        createXMLround();
	}
}
