/*
 * Test de l'algorithme d'appariement sur de petites matrices
 */

static char rcsid[] = "$Id: test.c,v 1.3 1994/07/26 19:30:35 bousch Exp $";

#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include "appari.h"

int i, j, n, *solution;
pen_t **penalites, resultat;

int main(void) {
    scanf("%d", &n);
    printf("Il y a %d joueurs\n", 2*n);
    penalites = (pen_t**)malloc(2*n*sizeof(pen_t*));
    for (i=0; i<2*n; i++) {
        penalites[i] = (pen_t*)malloc(2*n*sizeof(pen_t));
        for (j=0; j<2*n; j++)
            scanf("%lu",&penalites[i][j]);
    }
    solution = (int*)malloc(2*n*sizeof(int));
    resultat = apparie(n,penalites,solution);
    for (i=0; i<n; i++)
        printf("%d-%d\t%lu\n",solution[2*i],solution[2*i+1],
    penalites[solution[2*i]][solution[2*i+1]]);
    printf("\nTotal =\t%lu\n", resultat);
    return 0;
}

