// Headless test helper. Calls the installed KConfig authorization functions.
// Dynamic resolution avoids installing additional development packages.
#include <QCoreApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLibrary>
#include <QString>
#include <iostream>

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    QLibrary library(QStringLiteral("libKF6ConfigCore.so.6"));
    if (!library.load()) {
        std::cerr << library.errorString().toStdString();
        return 2;
    }
    using Function = bool (*)(const QString &);
    auto control = reinterpret_cast<Function>(library.resolve("_ZN11KAuthorized22authorizeControlModuleERK7QString"));
    auto action = reinterpret_cast<Function>(library.resolve("_ZN11KAuthorized9authorizeERK7QString"));
    if (!control || !action) {
        return 3;
    }
    QJsonObject controls, actions;
    for (const auto &name : {"kcm_wallpaper", "kcm_wallpaper.desktop", "kcm_kscreen", "kcm_kscreen.desktop",
                             "kcm_pulseaudio", "kcm_networkmanagement", "kcm_keyboard"}) {
        controls[QString::fromLatin1(name)] = control(QString::fromLatin1(name));
    }
    for (const auto &name : {"plasma/plasmashell/unlockedDesktop", "plasma/allow_configure_when_locked", "shell_access", "logout"}) {
        actions[QString::fromLatin1(name)] = action(QString::fromLatin1(name));
    }
    std::cout << QJsonDocument(QJsonObject{{"controls", controls}, {"actions", actions}}).toJson().toStdString();
}
