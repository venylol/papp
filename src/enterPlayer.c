/*
 * enterPlayer.c: entree d'un nom complet de joueur
 *
 * (EL) 10/02/2019 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32 Separation fullname+prenom lors de l'entree d'un nouveau joueur.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 19/03/2007 : Modification esthetique des chaines affichees (TAB).
 * (EL) 12/02/2007 : Modification de 'display_registered()', 'display_teams()'
 *                   pour qu'ils affichent le fullname du tournoi au debut.
 * (EL) 02/02/2007 : 'tieBreak' devient un double
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 *
 * enterPlayer.c: entry of a player fullname.
 *
 * (EL) 10/02/2019 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to be sure they are on 4 bytes.
 * (EL) 21/04/2008 : v1.32 fullname+firstname split when entering a new players.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 19/03/2007 : Minor modifications for displayed strings (TAB).
 * (EL) 12/02/2007 : Changes in 'display_registered()', 'display_teams()'
 *                   so they display the tournament fullname at start.
 * (EL) 02/02/2007 : Tie-break is now double type
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <ctype.h>
#include "global.h"
#include "player.h"
#include "more.h"



Players_list
        *registered_players   = NULL,
        *new_players   = NULL,
        *emigrant_players    = NULL,
        *team_captain = NULL;


#ifdef ENGLISH
#define BASE_JOUEURS_GROSSE  "I will not show you all the players in the database...\n" \
                             "Please type at least one letter before using the TAB\n"
#else
#define BASE_JOUEURS_GROSSE  "Je ne vais pas vous montrer toute la base des joueurs...\n"\
                             "Utilisez la touche TAB apres avoir tape au moins une lettre\n"
#endif


/*
 * Entree au clavier d'un fullname de joueur, avec affichage d'un prompt :
 * initial_buffer permet d'initialiser le tampon, et from_these_players
 * est la liste dans laquelle la fonction fait la completion quand
 * l'utilisateur tape [TAB] (passer NULL dans from_these_players pour
 * faire la completion dans toute la base).
 * Cette fonction reconnait les touches speciales suivantes :
 *
 *   ^C       vide le tampon, et quitte.
 *   ^D,Tab   affiche toutes les completions possibles, puis complete (si possible) le tampon.
 *   ^M,^J    quitte
 *   Esc,^X   vide le tampon.
 *
 * La fonction retourne alors un pointeur sur le tampon. Celui-ci est statique, et doit donc etre copie
 * quelque part avant que la fonction ne soit invoquee une nouvelle fois.
 * La longueur de la chaine ne peut exceder BUFFER_SIZE caracteres (sans compter le \0 final).
 *
 ****
 *
 * Keyboard entry of a player fullname with prompt display : initial_buffer is used to initialize the buffer
 * and from_these_players is the list to choose the completion from when user presses [TAB] (using NULL for
 * from_these_players makes completion from whole base).
 * This function recognise the following special keys:
 *
 *   ^C       empty buffer and quit.
 *   ^D,Tab   displays all possible completions and complete (if possible) the buffer
 *   ^M,^J    quit
 *   Esc,^X   empty the buffer
 *
 *  Function returns a pointer to the buffer. It is static so it must be copied somewhere before
 *  the function is called again. String length cannot exceed BUFFER_SIZE characters (without the trailing \0).
 *
 */

char *enter_player_name_kbd(const char *prompt, char *initial_buffer, Players_list *from_these_players) {
    long exitcode ;
    unsigned long l, i, answers_count,  answer_length,  j, nbc, nbl;
    static char buffer[BUFFER_SIZE+1];
    char c, d;
    static Players_list *completion_list = NULL;



    if (initial_buffer == NULL)
       buffer[0] = '\0';
    else {
    	if (buffer != initial_buffer) { 
	    	/* BUG du WOC 2019 quand on accouple/decouple -
	    	   WOC 2019 Bug when doing manual pairings */
    	strcpy(buffer,initial_buffer);
    	}
    }
    while (1) {
        printf("%s ", prompt);
        exitcode = read_Line_init(buffer, BUFFER_SIZE, 1);

        putchar('\n');
        if (exitcode == 0) /* Ok */
            return buffer;
        if (exitcode < 0)
            return "";  /* avorte */

        /*
         * L'utilisateur a tape sur Tab : il faut chercher les completions possibles du fullname
         ****
         * User pressed Tab: all possible completions of the fullname must be found
         */
        SeemsValidPlayerName(buffer);
        if (from_these_players == NULL) {
            if ( (strlen(buffer) == 0) && (countPlayersInDatabase() >= 100) ) {
                /* ne pas afficher toute la base des joueurs  :-/
                 ****
                 * Do not display all the player database  :-/
                 */

                printf(BASE_JOUEURS_GROSSE);
                if (completion_list==NULL)
                    completion_list = createList();
                else
                    emptyList(completion_list);
            } else
                completion_list = searchName(completion_list, buffer);
        } else
            completion_list = recherche_nom_dans_liste(completion_list, buffer, from_these_players);

        /* Combien de reponses ? - How many answers? */
        answers_count = completion_list->n;
        if (answers_count == 0) {
            /* Pas de completion: on laisse le buffer tel quel - No completion, buffer is left as it is */
            beep();
            continue;
        }
        assert(answers_count > 0);

        /* Determiner la longueur maximum des reponses - Calculate maximum length of answers */
        answer_length = 0;
        for (i = 0; i < answers_count; i++) {
            assert(completion_list->list[i]);
            l = strlen((completion_list->list)[i]->fullname);
            if (l > answer_length)
                answer_length = l;
        }

        /* Combien peut-on en mettre par ligne ? - How many can be displayed per line? */
        nbc = (nbrOfColumns-1) / (answer_length+1);
        /* Mais nous voulons qu'il y ait assez de lignes - Enough lines are needed */
        while (nbc>1 && 2*nbc*nbc>answers_count)
            --nbc;
        nbl = (answers_count + nbc - 1) / nbc;
        assert(nbc>0 && nbl>0);
        for (i=0; i<nbl; i++) {
            for (j=0; j<nbc && (l=i+nbl*j)<answers_count; j++)
                printf(" %-*s", (int)answer_length,
                    (completion_list->list)[l]->fullname);
            putchar('\n');
        }

        /* Completer le buffer - Complete buffer */
        for(l = strlen(buffer); ; l++) {
            c = ((completion_list->list)[0]->fullname)[l];
            if (c == '\0')
                goto b_comp;

            for (i=1; i<answers_count; i++) {
                d = ((completion_list->list)[i]->fullname)[l];
                if (tolower(c) != tolower(d))
                    goto b_comp;
            }
        }
b_comp:
        /* l contient la longueur de la plus grande completion - l has length of greatest completion */
        if (l > BUFFER_SIZE)
            l = BUFFER_SIZE;
        if (l >= strlen(buffer)) {
            strncpy(buffer, (completion_list->list)[0]->fullname, l);
            buffer[l] = '\0';
            assert(buffer[BUFFER_SIZE]==0);
        }
        /* Revenir au prompt - Come back to prompt */
    }
}

/*
 * Cette fonction est appelee par le programme principal, et invoque la fonction enter_player_name_kbd()
 * pour l'entree interactive des noms. Elle est quittee des que l'utilisateur entre un fullname vide
 * (par exemple s'il tape ^C).
 ****
 * This function is called by the main program and calls enter_player_name_kbd() function for the
 * interactive entry of names. It leaves as soon as the user enters an empty fullname (for example with ^C).
 */

#ifdef ENGLISH
# define ENTR_TAB   "Press [TAB] to complete a name\n\n"
# define ENTR_PROMPT    "Full name:"
# define ENTR_NELO  "Enter his/her ELO number:"
# define ENTR_NEWP  "Is this a new player (Y/N)? "
# define ENTR_NATION    "Which country? "
# define ENTR_PROP1 "Number %ld is free, "
# define ENTR_PROP2 "do you accept it (Y/N)? "
# define ENTR_CHOOSE    "Then choose another one:"
# define ENTR_AGAIN "This number is already allocated; please try again:"
# define ENTR_DUP   "This player has already been inscribed !"
# define LIST_HDR1  "There are %ld inscribed players:"
# define LIST_HDR2  "Ranking of the %ld players after round %ld:"
# define TEAM_HDR1  "There are %ld teams:"
# define TEAM_HDR2  "Ranking of the %ld teams after round %ld:"
#else
# define ENTR_TAB   "Appuyer sur [TAB] pour completer un nom\n\n"
# define ENTR_PROMPT    "Nom & prenom :"
# define ENTR_NELO  "Entrez le numero ELO du joueur :"
# define ENTR_NEWP  "Est-ce un nouveau joueur (O/N) ? "
# define ENTR_NATION    "Quelle est sa nationalite ? "
# define ENTR_PROP1 "Je vous propose le numero %ld, "
# define ENTR_PROP2 "etes-vous d'accord (O/N) ?"
# define ENTR_CHOOSE    "Alors veuillez m'en proposer un autre :"
# define ENTR_AGAIN "Ce numero est deja utilise, recommencez :"
# define ENTR_DUP   "Ce joueur est deja inscrit !"
# define LIST_HDR1  "Liste des %ld joueurs inscrits :"
# define LIST_HDR2  "Classement des %ld joueurs apres la ronde %ld :"
# define TEAM_HDR1  "Liste des %ld equipes :"
# define TEAM_HDR2  "Classement des %ld equipes apres la ronde %ld :"
#endif

void enter_players(void) {
    static Players_list *pl=NULL;
    Player *j;
    char *player_name, *country, *buffer;
    long i, n;
	char *ptr_last_space, *ptr_first_space ;
	char *firstName ;

    assert(registered_players != NULL);
    assert(new_players != NULL);
    assert(emigrant_players  != NULL);
    clearScreen();
    reverse_video(ENTR_TAB) ;
    country = new_string();

    buffer = "";
    while ((player_name = enter_player_name_kbd(ENTR_PROMPT, buffer, NULL))[0]) {
        buffer = "";
        if (!SeemsValidPlayerName(player_name)) {
           beep();
           buffer = player_name;
           continue;
        }

redo:
        /* Chercher si le joueur est dans la base - look if player is in the database */
        if (pl == NULL)
            pl = createList();
        else
            emptyList(pl);
        for (j = firstPlayer(); j; j=j->next)
            if (!compare_strings_insensitive(j->fullname, player_name)) {
                addPlayer(pl, j);
                printf(j->comment ?
                    "%ld\t%s {%s} -- %s\n" : "%ld\t%s {%s}\n",
                    j->ID,j->fullname,j->country,j->comment);
            }
        /* Combien de reponses ? - How many answers? */
        if (pl->n == 1)
            j = (pl->list)[0];
        if (pl->n > 1)
            do {
                /* Il y a ambiguite, nous demandons le ID ELO - Ambiguity, ask for ELO ID  */
                printf(ENTR_NELO);
                i = sscanf(read_Line(),"%ld", &n); putchar('\n');
                if (i < 1)
                    goto redo;
                j = NULL;
                for (i=0; i<pl->n; i++)
                    if ((pl->list)[i]->ID == n)
                        j = (pl->list)[i];
            } while (j==NULL);
        if (pl->n == 0) {
            /* Est-ce un nouveau joueur ou une erreur ? - Is it a new player or a mistake? */
            if (yes_no(ENTR_NEWP)==0) {
                buffer = player_name;
                continue;
            }

           /* Nationalite - Nationality */
entree_pays:
            printf(ENTR_NATION);
            strcpy(country, read_Line());
            removeLeftSpaces(country);
            if (country[0] == 0)
                puts(strcpy(country, default_country));
            else
            putchar('\n');
            removeLeftSpaces(country);
            if (country[0] == 0) {
               beep();
               goto entree_pays;
            }

            /* Il faut choisir un ID Elo - An ELO ID must be chosen */
            n = insertPlayer(country);


            /* Modification par Stephane Nicolet : nous ne demandons plus confirmation a l'utilisateur
             * du choix par Papp d'un nouveau ID : je crois que cette fonction n'etait _jamais_ utilisee
             * et que l'on acceptait _toujours_ le ID par defaut. Est-ce que je me trompe ?
             ****
             * Modification by Stephane Nicolet: don't ask the user anymore to confirm Papp's choice of
             * a new ID: I believe this function was _never_ used and that the default ID was _always_
             * accepted. Am I right?
            */

            /*
            printf(ENTR_PROP1, n);
            if ((yes_no(ENTR_PROP2) == 0)) {
              printf(ENTR_CHOOSE);
              for(;;) {
            i = sscanf(read_Line(), "%ld", &n); putchar('\n');
            if (i < 1 || n <= 0)
                {beep(); goto redo;}
            if (findPlayer(n))
              printf(ENTR_AGAIN);
            else
              break;
              }
            }
            */


            /* Creer un nouveau joueur - Create a new player */
            assert(player_name && player_name[0]);
			/* On decoupe le fullname complet en fullname + firstName (la derniere chaine sans espace)
			 ****
			 * The fullname is split into fullname + firstName (last string without spaces)
			 */
			ptr_last_space = strrchr(player_name, ' ') ;
			ptr_first_space =  strchr(player_name, ' ') ;
			if ((ptr_last_space != NULL) && (ptr_first_space != player_name)) {
                COPY(ptr_last_space + 1, &firstName) ;
				*ptr_last_space = '\0' ; /* On coupe - Let's cut */
			} else {
				firstName = NULL ;
			}
            j = new_player(n, player_name, firstName, NULL, country, 0, NULL, 1);
            assert(n == j->ID);
            addPlayer(new_players, j);
        }
        /* Inserer le joueur dans la liste des joueurs inscrits; nous devons d'abord verifier
         * qu'il n'y est pas deja; ce sera automatiquement le cas pour les nouveaux joueurs
         ****
         * Insert player in the list of registered players; we must first check it's not already the case;
         * it will automatically be the case for new players.
         */
        if (inscription_ID(j->ID)<0)    /* pas inscrit - not registered */
            register_player(j->ID);
        else {
            puts(ENTR_DUP);  /* deja inscrit - already registered */
            beep();
            printf("\n");
        }
    } /* while */
}

/*
 * Ajoute un joueur de ID Elo 'ID' a la liste des joueurs inscrits et renvoie un ID d'inscription
 * (le precedent ID d'inscription si le joueur etait deja inscrit).
 * Renvoie -1 si le joueur n'est pas dans la base.
 ****
 * Add a player with ELO ID 'ID' to the list of registered players and returns an inscription ID
 * (the former ID if the player is already registered).
 * Returns -1 if the player is not in the database.
 */

long register_player(long ELO_ID) {
    Player *p;
    long n;

    n = inscription_ID(ELO_ID);
    if (n >= 0) {
        assert(0 <= n && n < MAX_REGISTERED);
        present[n]   = 1;       /* Le joueur est present - player is present */
        return n;
    }
    if (n < 0)  {
        p = findPlayer(ELO_ID);
        if (p == NULL)
            return -1;      /* le joueur n'est pas dans la base - player not in database */
        addPlayer(registered_players, p);
        n = inscription_ID(ELO_ID);
    }

    assert(0 <= n && n < MAX_REGISTERED);
    present[n]   = 1;           /* Le joueur est present - player is present */
    score[n]     = 0;           /* zero point                                */
    nbr_discs[n]  = ZERO_DISC;  /* zero pion - no discs                      */
    tieBreak[n] = 0.0;          /* on ne sait jamais - you never know        */
    last_float[n] = 0;          /* il n'a pas flotte - no floating yet       */
    return n;
}

/*
 * Important : cet ordre de tri doit etre le meme que celui implemente dans
 * la fonction compar() de crosstable.c
 ****
 * Important: this sort order must be the same as the one implemented in the compar() function of crosstable.c
 */

int sort_players(const void *ARG1, const void *ARG2) {
	long *n1 = (long *) ARG1 ;
 	long *n2 = (long *) ARG2 ;
    long d, j1=(*n1), j2=(*n2);

    if ((d = score[j2] - score[j1]) != 0)
        return d;
/*  if (DIFFERENT_SCORES(tieBreak[j2],tieBreak[j1])) {
        if SCORE_IS_LARGER(tieBreak[j2],tieBreak[j1])
            return 1;
        if SCORE_IS_SMALLER(tieBreak[j2],tieBreak[j1])
            return -1;
    } */
    if (tieBreak[j2] != tieBreak[j1]) {
        if (tieBreak[j2] > tieBreak[j1])
            return 1 ;
        if (tieBreak[j2] < tieBreak[j1])
            return -1 ;
    }
    if (DIFFERENT_SCORES(nbr_discs[j2],nbr_discs[j1])) {
        if SCORE_IS_LARGER(nbr_discs[j2],nbr_discs[j1])
            return 1;
        if SCORE_IS_SMALLER(nbr_discs[j2],nbr_discs[j1])
            return -1;
    }
    return compare_strings_insensitive(registered_players->list[j1]->fullname,
        registered_players->list[j2]->fullname);
}

static int sort_teams(const void *ARG1, const void *ARG2) {
	long *n1 = (long *) ARG1 ;
 	long *n2 = (long *) ARG2 ;
    long d, j1=(*n1), j2=(*n2);

    if ((d = team_score[j2] - team_score[j1]) != 0)
        return d;
    else
        return compare_strings_insensitive(team_captain->list[j1]->country,
            team_captain->list[j2]->country);
}

/*
 * Affichage de la liste des joueurs inscrits - display list of registered players
 */
void display_registered(const char *filename) {
    long i, _i, nbi, draws, len_dep;
    char string[512], string2[1024];
    char *tournamentName ;

    assert(registered_players != NULL);
    assert(new_players != NULL);
    assert(emigrant_players  != NULL);

    /* Peut-etre devons-nous sauvegarder l'etat - Maybe should we save state? */
    if (immediate_save)
        save_registered();
    nbi = registered_players->n;
    tieBreak_computation();

    more_init(filename);

    /* Pour afficher le fullname du tournoi en tete - to display tournament fullname at head */
    tournamentName = malloc(strlen(tournament_name)+15) ;
    if (tournamentName != NULL) {
        sprintf(tournamentName, "*** %s ***\n", tournament_name) ;
        more_line(tournamentName) ;
        free(tournamentName) ;
    }

    sprintf(string, current_round? LIST_HDR2 : LIST_HDR1, nbi, current_round);
    more_line(string);
    more_line("");          /* ligne blanche - empty line */

    if (nbi > 0) {
        long    table[MAX_REGISTERED];
        long    dernier_score = -1;
        double  dernier_departage = -1.0;
        Player *j;

        for (i=0; i<nbi; i++)
            table[i] = i;
        SORT(table, nbi, sizeof(long), sort_players);

        /* y a-t-il des nulles a afficher ? - Are there any draws to display? */
        draws = 0; len_dep = 0;
        for (i=0; i<nbi; i++) {
            draws |= (score[table[i]] % 2);
            len_dep = max_of(len_dep, strlen(tieBreak2string(tieBreak[i])));
        }

        /* Afficher la liste triee - Display sorted list */
        for (i=0; i<nbi; i++) {
            _i = table[i];
            assert(_i >= 0 && _i < nbi);
            j  = registered_players->list[_i];

            sprintf(string2, "[%s]", tieBreak2string(tieBreak[_i]));
            sprintf(string, "%*s", (int)len_dep + 2, string2);

            /* Meme score que le joueur precedent? - Same score as previous player? */
            if (dernier_score != score[_i]) {
                if (score[_i] >= 3) {
                    if ((score[_i] % 2) == 1)
                        sprintf(string2,"%3ld:%4ld.5 pts %s", i+1, (score[_i] / 2 ), string);
                    else
                        if (draws)
                            sprintf(string2,"%3ld:%4ld.  pts %s", i+1, (score[_i] / 2 ), string);
                        else
                            sprintf(string2,"%3ld:%4ld pts %s", i+1, (score[_i] / 2 ), string);
                }
                else {
                    if ((score[_i] % 2) == 1)
                        sprintf(string2,"%3ld:%4ld.5 pt  %s", i+1, (score[_i] / 2 ), string);
                    else
                        if (draws)
                            sprintf(string2,"%3ld:%4ld.  pt  %s", i+1, (score[_i] / 2 ), string);
                        else
                            sprintf(string2,"%3ld:%4ld pt  %s", i+1, (score[_i] / 2 ), string);
                }
            }
            else {
                if (dernier_departage == tieBreak[_i])
                    sprintf(string, "%*s", (int)len_dep + 2, "");
                if (draws)
                    sprintf(string2, "%14s %s", "", string);
                else
                    sprintf(string2, "%12s %s", "", string);
            }

            dernier_score = score[_i];
            dernier_departage = tieBreak[_i];

            /* Nom, prenom, ID, pays... - Name, firstname, ID, country... */
            sprintf(string,
                compare_strings_insensitive(j->country, default_country)==0 ?
              "%s  %c%s (%ld)" : "%s  %c%s (%ld) {%s}", string2,
              present[_i]? ' ':'-', j->fullname,
              j->ID, j->country);
            more_line(string);
        }
    }   /* if nbi>0 */
    more_close();
}

void  display_teams(const char *filename) {
    long i, i_ , nbi, nbr_teams, found;
    char string[256];
    long teams[MAX_REGISTERED];
    char *tournamentName ;

    /* Peut-etre devons-nous sauvegarder l'etat - Maybe should we save state? */
    if (immediate_save)
        save_registered();
    nbi = registered_players->n;
    tieBreak_computation();

    /* Pour chaque equipe presente, on garde une reference sur l'un des representants de cette equipe :
     * le "capitaine". Cela permet d'acceder facilement au fullname de l'equipe pour les tris
     * et les affichages
     ****
     * For each present team, a reference to one of its memeber is kept: the 'captain'. It enables easy access
     * to the team fullname for sorting and displaying.
     */
    if (team_captain == NULL)
        team_captain = createList();
    else
        emptyList(team_captain);

    assert(registered_players != NULL);
    assert(team_captain != NULL);

    /* Ici, le capitaine de l'equipe nationale sera simplement le premier joueur inscrit de ce pays
     ****
     * Here, the national team captain will just be the first registered player from the country
     */
    nbr_teams = 0;
    for (i = 0; i < registered_players->n; i++) {
        found = 0;
        for (i_ = 0; i_ < team_captain->n; i_++)
            if (!compare_strings_insensitive(registered_players->list[i]->country,
                team_captain->list[i_]->country)) {
                found = 1;
                team_score[i_] += score[i];
            }
        if (!found) {
            addPlayer(team_captain, registered_players->list[i]);
            team_score[nbr_teams] = score[i];
            nbr_teams++;
        }
    }


    more_init(filename);

    /* Pour afficher le fullname du tournoi en tete - to display tournament fullname at head */
    tournamentName = malloc(strlen(tournament_name)+15) ;
    if (tournamentName != NULL) {
        sprintf(tournamentName, "*** %s ***\n", tournament_name) ;
        more_line(tournamentName) ;
        free(tournamentName) ;
    }

    sprintf(string, current_round? TEAM_HDR2 : TEAM_HDR1, nbr_teams, current_round);
    more_line(string);
    more_line("");          /* ligne blanche - empty line */

    if (nbr_teams > 0) {

        for (i=0; i<nbr_teams; i++)
            teams[i] = i;
        SORT(teams, nbr_teams, sizeof(long), sort_teams);

        for (i=0; i<nbr_teams; i++) {
            i_ = teams[i];
            assert(i_ >= 0 && i_ < nbr_teams);

            /* Nom du pays, score de l'equipe... */
            if ((team_score[i_] % 2) == 1)
                sprintf(string, "  %-7s  %5ld.5 pts",
                            team_captain->list[i_]->country,
                            team_score[i_]/2);
            else
                sprintf(string, "  %-7s  %5ld	pts",
                            team_captain->list[i_]->country,
                            team_score[i_]/2);

            more_line(string);
        }

    }   /* if nbr_teams>0 */
    more_close();

    emptyList(team_captain);
}

/*
 * Renvoie le ID d'inscription du joueur de ID Elo 'ELO_ID', ou -1
 * si le joueur n'est pas encore inscrit.
 ****
 * Returns inscription ID of the player with ELO IS 'ELO_ID', or -1 if the player isn't registered
 */

long inscription_ID(long ELO_ID) {
    long i;

    if (!ELO_ID)
        return -1 ;
    for (i=0; i<registered_players->n; i++)
    if (registered_players->list[i]->ID == ELO_ID)
        return i;
    /* pas trouve - not found */
    return -1;
}

/*
 * Modifie la nationalite d'un joueur de ID Elo 'ELO_ID'. Renvoie -1 si le joueur n'est pas dans la base.
 ****
 * Change a player's nationality for a player with ELO ID 'ELO_ID'. Returns -1 if player not in database.
 */

long change_nationality(long ELO_ID, const char *new_country) {
    Player *j;

    j = findPlayer(ELO_ID);
    if (j == NULL)
        return -1;
    if (compare_strings_insensitive(j->country, new_country))
        COPY(new_country, &j->country);
    return ELO_ID;
}
