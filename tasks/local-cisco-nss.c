#define _GNU_SOURCE
#include <nss.h>
#include <pwd.h>
#include <stdio.h>
#include <string.h>

enum nss_status
_nss_lab_cisco_initgroups_dyn(const char *user, gid_t primary, long int *start,
                             long int *size, gid_t **groups, long int limit, int *err)
{
    (void) start;
    (void) size;
    (void) groups;
    (void) limit;
    (void) err;
    if (strcmp(user, "cisco") != 0 || primary < 500)
        return NSS_STATUS_NOTFOUND;

    FILE *file = fopen("/etc/passwd", "re");
    if (file == NULL)
        return NSS_STATUS_NOTFOUND;
    struct passwd entry;
    struct passwd *result;
    char buffer[8192];
    enum nss_status status = NSS_STATUS_NOTFOUND;
    while (fgetpwent_r(file, &entry, buffer, sizeof buffer, &result) == 0 && result != NULL) {
        if (strcmp(entry.pw_name, user) == 0) {
            if (entry.pw_uid >= 400 && entry.pw_uid < 500 && entry.pw_gid == primary
                && strcmp(entry.pw_dir, "/var/lib/cisco-lab-local/cisco") == 0
                && strcmp(entry.pw_shell, "/bin/bash") == 0)
                status = NSS_STATUS_SUCCESS;
            break;
        }
    }
    fclose(file);
    return status;
}
