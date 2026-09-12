/*
 * discs.c: Toutes les operations sur les scores
 *
 * (EL) 25/10/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 04/02/2007 : Reecriture de 'tieBreak2string' pour afficher
 *                   un flottant avec une eventuelle decimale.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
****
 *
 * discs.c: operations on scores
 *
 * (EL) 25/10/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 04/02/2007 : Rewriting of 'tieBreak2string' to display
 *                   a float with an optional decimal point.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 */

#include <assert.h>
#include "changes.h"
#include "discs.h"
#include "global.h"


long discsTotal = 64;

/*
 * fonctions pour l'affichage - display functions
 */
char *discs2string(discs_t v) {
    char *result;

    result = new_string();
    assert(result != NULL);

#ifdef USE_HALF_DISCS
    if (v.half_discs % 2)
        sprintf(result, "%0*ld.5" , (int)scores_digits_number , v.half_discs / 2);
    else
        sprintf(result, "%0*ld" , (int)scores_digits_number , v.half_discs / 2);
#else
    sprintf(result, "%0*ld" , (int)scores_digits_number , v);
#endif
    return result;
}


char *tieBreak2string(double Dep) {
    char *result;
    long   rounding ;

    result = new_string();
    assert( result != NULL);

/* #ifdef USE_HALF_DISCS
    if (v.half_discs % 2)
        sprintf(result, "%ld.5" , v.half_discs / 2);
    else
        sprintf(result, "%ld" , v.half_discs / 2);
#else
    sprintf(result, "%ld" ,  v);
#endif
*/
    /* On arrondit d'abord a une decimale en multipliant par 10 le departage
     ****
     * One decimal rounding by multipying tie-break by 10
     */
    rounding = (long) 10*(Dep+0.049999) ;
    if ( rounding%10 == 0 )
        sprintf(result, "%.0f", (float) rounding/10.0);  /* on n'affiche pas les decimales - no decimal displayed */
    else
        sprintf(result, "%.1f", (float) rounding/10.0); /* une decimale arrondie - one rounded decimal is displayed */
    return result;
}


/*
 * understandScore : conversion d'une string en pions pour l'entree des resultats
 *  Renvoie : 1 si PAPP a reussi a interpreter la string comme un score.
 *            0 sinon
 *  Entree :
 *      <string> : chaine de caracteres
 *  Sorties :
 *      <value> et <relative> : quand la fonction reussit, <value> contient le score lu et
 *      <relative> vaut 1 si le score etait rentre en relatif, 0 sinon.
 *
 *****
 *
 * understandScore: convert a string into discs when entering results
 *  returns: 1 if PAPP succeeded to interpret the string as a score
 *           0 otherwise
 *  input:
 *      <string> : string of characters
 *  output:
 *      <value> and <relative>: when function succeeds, <value> contains read score and
 *      <relative> values 1 if the score was given in a relative way, 0 otherwise
 */
long understandScore(const char *string, discs_t *value, long *relative) {
    discs_t score;
    long n, readRelative;
    double f;
    char parasiteBuffer[100];


    assert(string != NULL);

    switch (string[0]) {
    case '=':
        score = RELATIVE_SCORE_TO_ABSOLUTE(INTEGER_TO_SCORE(0));
        readRelative = 1;
        break;
    case '+':
        if (sscanf(string+1, "%ld%s" , &n, parasiteBuffer) == 1) {
            score = RELATIVE_SCORE_TO_ABSOLUTE(INTEGER_TO_SCORE(n));
            readRelative = 1;
            break;
        } else
            return 0;  /* pas de score, ou des caracteres parasites - no score or parasite characters */
        break ;
    case '-':
        if (sscanf(string+1, "%ld%s" , &n , parasiteBuffer) == 1) {
            score = RELATIVE_SCORE_TO_ABSOLUTE(INTEGER_TO_SCORE(-n));
            readRelative = 1;
            break;
        } else
            return 0;  /* pas de score, ou des caracteres parasites - no score or parasite characters */
        break ;
    default:
        if (sscanf(string, "%le%s" , &f, parasiteBuffer) == 1)
          { score = FLOAT_TO_SCORE(f); readRelative = 0; }
        else
          return 0;  /* pas de score, ou des caracteres parasites - no score or parasite characters */
    }

    *value = score;
    *relative = readRelative;
    return 1;
}
