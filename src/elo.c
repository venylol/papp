/*
 * elo.c: Creation d'un fichier Elo - Creation of an ELO file
 *
 * (EL) 30/10/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, ajout du classement ELO dans le classement du tournoi.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 18/08/2008 : v1.33, ajout de la possibilite de rajouter des parties dans le fichier ELO
                     avec la fonction "enter_aditionnal_games()".
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, Le format des resultats du fichier ELO se conforme
                     au nouveau standard defini par la WOF.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : changement des 'fopen()' en 'myfopen_in_subfolder()'
 * (EL) 12/02/2007 : Changement de 'cree_fichier_elo()'. On ne demande plus
 *                   le fullname du tournoi puiqu'on l'a deja.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, lecture du fichier 'nouveaux' en faisant
 *                   attention aux fins de lignes
 ****
 *
 * (EL) 30/10/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, adding the ELO standings in the tournament one.
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 18/08/2008 : v1.33, added the possibility to add additional games in the ELO file
 * 					 with function "enter_aditionnal_games()"
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to be sure they are on 4 bytes.
 * (EL) 21/04/2008 : v1.32, ELO file format to respect new WOF ELO file format.
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : change all 'fopen()' to 'myfopen_in_subfolder()'
 * (EL) 12/02/2007 : Changing 'create_ELO_file()'. We already have tournament fullname.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, read 'nouveaux' file and beware of end-of-line characters.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <time.h>
#include <ctype.h>
#include "couplage.h"
#include "global.h"
#include "version.h"
#include "more.h"
#include "player.h"


#ifdef ENGLISH
	#define ELO_PROMPT_1       "Creating an ELO file"
	#define ELO_PROMPT_2       "Enter the filename [%s]: "
/*# define ELO_PROMPT_3     "Name of the tournament : "*/
	#define ELO_PROMPT_4		"Country: "
	#define ELO_PROMPT_5		"Date (DD/MM/YYYY) [%s]: "
	#define ELO_PROMPT_6		"Who is sending the results (your name): "
	#define CANT_OPEN           "Can't open file"
	#define THANKS				"\n\nResults file %s created.\nPlease send it to Emmanuel.Lazard@katouche.fr\nfor inclusion in the rating list.\n\n"
	#define HIT_ANY_KEY        "Hit any key to return to the main menu."
	#define NEW_PLAYERS        "New players"
	#define NEW_PLAYERS_END    "End of new players"
	#define ELO_ADD_GAMES   "You can now add results to the ELO file.\n\n"
	#define ENTER_BLACK          "\n\nBlack player (return to exit)?"
	#define ENTER_BLACK_SCORE    "Black score?"
	#define ENTER_WHITE         "\nWhite player?"
	#define ENTER_WHITE_SCORE   "White Score?"
#else
	#define ELO_PROMPT_1       "Creation d'un fichier ELO"
	#define ELO_PROMPT_2       "Entrez le nom du fichier [%s] : "
/*# define ELO_PROMPT_3     "et le fullname complet du tournoi : "*/
	#define ELO_PROMPT_4		"Pays : "
	#define ELO_PROMPT_5		"Date (DD/MM/YYYY) [%s] : "
	#define ELO_PROMPT_6		"Qui envoie le fichier (votre nom) : "
	#define CANT_OPEN          "Impossible d'ouvrir"
	#define THANKS				"\n\nFichier de resultats %s cree.\nMerci de l'envoyer a Emmanuel.Lazard@katouche.fr\npour inclusion dans le classement.\n\n"
	#define HIT_ANY_KEY        "Pressez une touche pour revenir au menu principal."
	#define NEW_PLAYERS        "Nouveaux joueurs"
	#define NEW_PLAYERS_END    "Fin des nouveaux joueurs"
	#define ELO_ADD_GAMES   "Vous pouvez maintenant entrer des parties a ajouter au fichier ELO.\n\n"
	#define ENTER_BLACK          "\n\nJoueur noir (return pour quitter) ?"
	#define ENTER_BLACK_SCORE    "Score noir ?"
	#define ENTER_WHITE         "\nJoueur blanc ?"
	#define ENTER_WHITE_SCORE   "Score blanc ?"

#endif

/*
 * On recopie le contenu du fichier 'nouveaux' dans le FILE* passe en parametre
 ****
 * Copy 'nouveaux' file into the given FILE* parameter
 */
void copy_nouveaux_file(FILE *fp_dest) {
    FILE *newPlayers_file;
    unsigned long i, endOfLine ;
    long c ;
    char new_player_name[256];

    newPlayers_file = myfopen_in_subfolder(new_players_filename, "rb", "", 0, 0);
    if (newPlayers_file != NULL) {
        fprintf(fp_dest,"\n%% " NEW_PLAYERS " :\n%%\n");
        do {
            i=0 ; endOfLine=0 ; /* on commence une nouvelle ligne - Start of new line */
            do {
                c = fgetc(newPlayers_file) ;
                if (c != EOF && c != 0x0D && c != 0x0A && i<(sizeof(new_player_name)-1))
                    new_player_name[i++] = (char) c ;
                else
                    endOfLine = 1 ;
            } while (!endOfLine) ;
            new_player_name[i] = 0 ;
            if (strlen(new_player_name) != 0) {
                fprintf(fp_dest,"%%_%%");
                fprintf(fp_dest,"%s\n", new_player_name);
            }
        } while (c != EOF) ;

        fprintf(fp_dest,"%%\n%% " NEW_PLAYERS_END "\n\n");
        fclose(newPlayers_file);
    }
}

/*
 * Recopie la liste des joueurs dans le FILE* passe en parametre
 ****
 * Copy list of players into the given FILE* parameter
 */
void copy_registered_list(FILE *fp_dest) {
	long i, _i, nbi ;

    assert(registered_players != NULL);
    assert(new_players != NULL);

    nbi = registered_players->n;

    if (nbi > 0) {
        long    table[MAX_REGISTERED];
        Player *j;

        for (i=0; i<nbi; i++)
            table[i] = i;
        SORT(table, nbi, sizeof(long), sort_players);

		fprintf(fp_dest, "\n%%        ID, NAME, Firstname, COUNTRY, score, disc-count, rating\n") ;
		for (i=0; i<nbi; i++) {
			_i = table[i];
            assert(_i >= 0 && _i < nbi);
            j  = registered_players->list[_i];
			fprintf(fp_dest, "%%_%% ") ;
			fprintf(fp_dest, "%c", (j->newPlayer ? '+' : ' ')) ;
			fprintf(fp_dest, "%6ld, %s, %s, %s", j->ID, j->family_name, j->firstname, j->country) ;
			/* Sauvegarde scores - Save scores */
			if ((score[_i] % 2) == 1)
				fprintf(fp_dest,", %2ld.5", (score[_i] / 2 ));
			else
				fprintf(fp_dest,", %2ld", (score[_i] / 2 ));
			/* Sauvegarde pions - Save discs */
			fprintf(fp_dest, ", %s, %ld\n", discs2string(nbr_discs[_i]), j->rating) ;
		}
		fprintf(fp_dest, "\n\n") ;
	}
}

int read_score(char *prompt) {
	int number ;
	unsigned int i ;
	char buffer[100] ;

	while (1) {
		printf("%s", prompt) ;
		strcpy(buffer, read_Line());
		/* Verifions qu'il n'y a que des chiffres - Check there are only digits */
		if (strlen(buffer) == 0) {
			printf("\n") ;
			continue ;
		}
		number = 1 ;
		for (i=0 ; i<strlen(buffer) ; i++) {
			if (!isdigit(buffer[i])) {
				number = 0 ;
				break ;
			}
		}
		if (!number) {
			continue ;
			printf("\n") ;
		}
		number = atoi(buffer) ;
		if ( (number<0) || (number>64)) {
			continue ;
			printf("\n") ;
		}
		return number ;
	}
}


/*
 * Permet a l'utilisateur d'entrer des parties supplementaires (ex: finales,...)
 * dans le fichier ELO.
 ****
 * Enables the user to add additional games (ex: finals...) to the ELO file.
 */
void enter_aditionnal_games(FILE *fp_dest) {
	long BlackPlayer, WhitePlayer ;
	int BlackScore, WhiteScore ;
	char *buffer ;
	char symb ;
	printf(ELO_ADD_GAMES) ;
	fprintf(fp_dest, "\n\n%%%%Added results\n") ;
	while (1) {
		BlackPlayer = selectPlayerFromKeyboard(ENTER_BLACK, registered_players, &buffer) ;
		if ((BlackPlayer == ALL_PLAYERS) || (BlackPlayer<0))
			break ;
		BlackScore = read_score(ENTER_BLACK_SCORE) ;
		do {
			WhitePlayer = selectPlayerFromKeyboard(ENTER_WHITE, registered_players, &buffer) ;
		} while (WhitePlayer <0) ;
		WhiteScore = read_score(ENTER_WHITE_SCORE) ;
		/* Sauvegarder le resultat - Save result */
		if (BlackScore > WhiteScore)
			symb = '>' ;
		else if (BlackScore < WhiteScore)
			symb = '<' ;
		else
			symb = '=' ;
		fprintf(fp_dest, "%6ld (%02d)%c(%02d) %6ld %c\n", BlackPlayer, BlackScore, symb, WhiteScore, WhitePlayer, 'B');
	}
}

void create_ELO_file(void) {
    char filename[256] = "" ;
	/*  char nom_tournoi[256]; */
	char country_name[256] ="" ;
	char date_elo[256] ="" ;
	char sender[256] ="" ;
	char strtmp[256] ="" ;
    FILE *fp;
    struct tm *t;
    time_t now;
    long round0, n1, n2;
    discs_t v;
	char c =' ';

    if (current_round < 1)
        return;
    clearScreen();
    /* Creation d'un fullname de fichier par defaut - Creation of full filename */
	if (strlen(tournament_date) > 9) {
		strcpy(filename, &tournament_date[6]) ;/* annee - year */
		filename[4]=tournament_date[3] ; /* mois - month */
		filename[5]=tournament_date[4] ;
		filename[6]=tournament_date[0] ; /* jour - day */
		filename[7]=tournament_date[1] ;
		filename[8] = '_' ;
		filename[9] = '\0' ;
	}
	strcat(filename, subfolder_name) ;
	strcpy(&filename[strlen(filename)-1], ".ELO") ;

    printf(ELO_PROMPT_1 "\n\n" ELO_PROMPT_2, filename);
    strcpy(strtmp, read_Line());
	if (strtmp[0])
		strcpy(filename, strtmp) ;
	if (filename[0] == 0)
        return;                 /* Annulation - cancel */
    putchar('\n');

	/* Nom du pays - Country name */
	printf(ELO_PROMPT_4);
	strcpy(country_name, read_Line());
	if (!country_name[0]) /* vide ? - empty? */
		strcpy(country_name, "??") ;
	putchar('\n');

	/* Date du tournoi - tournament date */
	printf(ELO_PROMPT_5, tournament_date);
	strcpy(date_elo, read_Line());
	if (!date_elo[0])
		strcpy(date_elo, tournament_date) ;
	putchar('\n');

	/* Nom de l'expediteur - Sender name */
	printf(ELO_PROMPT_6);
	strcpy(sender, read_Line());
	if (!sender[0]) /* vide ? - empty? */
		strcpy(sender, "??") ;
	putchar('\n');

    block_signals();
    fp = myfopen_in_subfolder(filename, "w", subfolder_name, 1, 0);
    if (fp == NULL) {
        printf(CANT_OPEN " '%s'\n",filename);
        goto attendre_touche;
    }
    /* Inscrire le fullname du tournoi - put tournament fullname */
    if (tournament_name[0])
        fprintf(fp, "%%%%Tournament: %s\n", tournament_name);
	else
        fprintf(fp, "%%%%Tournament: \n");
	fprintf(fp, "%%%%Country: %s\n", country_name);
	fprintf(fp, "%%%%Date: %s\n", date_elo);
	fprintf(fp, "%%%%Sender: %s\n\n", sender);


    /* Le createur et la date de creation - Creator and creation date */
    fprintf(fp, "%%%%Creator: %s\n", VERSION);

    time(&now);
    t = localtime(&now);
    assert(t != NULL);
    fprintf(fp, "%%%%CreationDate: %04d/%02d/%02d %02d:%02d\n",
			t->tm_year+1900, t->tm_mon+1, t->tm_mday,
			t->tm_hour, t->tm_min);

	/* recopier le fichier nouveaux - copy new players file */
	/* Now unnecessary as it's in the players list */
	/*  copy_nouveaux_file(fp) ;*/
	copy_registered_list(fp) ;

	/* Boucle sur les rondes - loop on all rounds */
    for (round0 = 0; round0 < current_round; round0++) {
        fprintf(fp,"\n%%Round: %ld\n\n", round0+1);
        round_iterate(round0);
        while (next_couple(&n1, &n2, &v)) {
            if (IS_VICTORY(v))
				c = '>' ;
			if (IS_DRAW(v))
				c = '=' ;
            if (IS_DEFEAT(v))
				c = '<';
			fprintf(fp, "%6ld (%s)%c(%s) %6ld %c\n", n1, discs2string(v),c, discs2string(OPPONENT_SCORE(v)), n2, 'B');
        }
    }
	enter_aditionnal_games(fp) ;

    fclose(fp);
	printf(THANKS, filename) ;
    printf(HIT_ANY_KEY "\n");
	attendre_touche:
	unblock_signals();
    (void) read_key();
}
